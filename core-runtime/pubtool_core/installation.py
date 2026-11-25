"""Immutable core snapshots. Never remove user data or an older core version."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path


def verify_core(root: Path):
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("核心目錄不可為連結")
    manifest_path = root / "core-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8-sig"))
    if (not isinstance(manifest, dict) or manifest.get("format") != 1
            or not isinstance(manifest.get("core_version"), str)
            or not re.fullmatch(r"\d+\.\d+\.\d+", manifest["core_version"])):
        raise ValueError("無效核心 manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("核心套件缺少完整性清單")
    seen = set()
    for entry in files:
        if (not isinstance(entry, dict) or type(entry.get("bytes")) is not int or entry["bytes"] <= 0
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("核心清單欄位無效")
        relative = entry.get("path")
        if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                or Path(relative).is_absolute() or ".." in Path(relative).parts or relative in seen):
            raise ValueError("核心檔案路徑無效或重複")
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
            raise ValueError("核心檔案遺失或超出範圍")
        if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"核心檔案雜湊不符：{relative}")
        seen.add(relative)
    if not {"pubtool_core/__init__.py", "pubtool_core/bootstrap.py", "bootstrap/main.py", "requirements-core.txt"} <= seen:
        raise ValueError("核心清單缺少必要啟動檔案")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
    if actual != seen | {"core-manifest.json"}:
        raise ValueError("核心套件出現未列入清單的檔案")
    return manifest, hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def install_bundled_core(source: Path, data_dir: Path):
    manifest, digest = verify_core(source)
    cores = data_dir / "cores"
    cores.mkdir(parents=True, exist_ok=True)
    target = cores / manifest["core_version"]
    if target.exists():
        _, existing_digest = verify_core(target)
        if existing_digest != digest:
            raise ValueError("同一核心版本已有不同內容，請提升 core_version；不覆寫既有核心")
        return target
    lock = cores / "install.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("另一個核心安裝操作持有 install.lock，請先完成該操作") from exc
    temporary = cores / (".install-" + uuid.uuid4().hex)
    try:
        os.close(lock_fd)
        temporary.mkdir()
        for entry in [*manifest["files"], {"path": "core-manifest.json"}]:
            relative = entry["path"]
            output = temporary / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write((source / relative).read_bytes())
        verify_core(temporary)
        if not target.resolve().is_relative_to(cores.resolve()) or target.exists():
            raise ValueError("核心目標路徑改變，不覆寫")
        temporary.rename(target)
        pointer = cores / ("current." + uuid.uuid4().hex + ".json")
        with pointer.open("x", encoding="utf-8") as output:
            json.dump({"core_version": manifest["core_version"], "manifest_sha256": digest}, output)
        pointer.replace(cores / "current.json")
        return target
    finally:
        lock.unlink(missing_ok=True)
        # Failed .install-* is retained for diagnosis, not recursively deleted.
