"""Manifest compatibility and explicit package loading, before domain startup."""
from __future__ import annotations

import importlib
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

from . import CORE_VERSION, CORE_API_VERSION, CAPABILITIES
from .api import PackageRegistrar


class IncompatiblePackage(ValueError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", value):
        raise IncompatiblePackage("Invalid semantic version")
    return tuple(int(part) for part in value.split("-", 1)[0].split("."))


def contained_path(root: Path, relative: str, *, directory=False):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise IncompatiblePackage("Invalid package path")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise IncompatiblePackage("Package path escapes root")
    exists = result.is_dir() if directory else result.is_file()
    if not exists:
        raise IncompatiblePackage(f"Missing package path: {relative}")
    return result.resolve()


def read_manifest(root: Path):
    try:
        manifest = json.loads((root / "app-manifest.json").read_text("utf-8-sig"))
        if not isinstance(manifest, dict) or manifest.get("format") != 1 or manifest.get("package_id") != "PUB-TOOL":
            raise IncompatiblePackage("Unsupported application manifest")
        version_tuple(manifest["package_version"])
        requirement = manifest["requires_core"]
        if (type(requirement["api_min"]) is not int or type(requirement["api_max"]) is not int
                or not requirement["api_min"] <= CORE_API_VERSION <= requirement["api_max"]):
            raise IncompatiblePackage(f"功能包需要不同核心 API；目前為 {CORE_API_VERSION}")
        if version_tuple(CORE_VERSION) < version_tuple(requirement["min_version"]):
            raise IncompatiblePackage("請先安裝功能包要求的執行器核心")
        capabilities = requirement["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(x, str) for x in capabilities) or not set(capabilities) <= CAPABILITIES:
            raise IncompatiblePackage("功能包要求未提供的核心能力")
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", manifest["entrypoint"]):
            raise IncompatiblePackage("Invalid package entrypoint")
        contained_path(root, manifest["backend_root"], directory=True)
        frontend = contained_path(root, manifest["frontend_root"], directory=True)
        contained_path(frontend, manifest["frontend_entry"])
        contract = json.loads(contained_path(frontend, "ui-contract.json").read_text("utf-8-sig"))
        if (not isinstance(contract, dict) or contract.get("format") != 1
                or type(contract["api_min"]) is not int or type(contract["api_max"]) is not int
                or not contract["api_min"] <= CORE_API_VERSION <= contract["api_max"]):
            raise IncompatiblePackage("前端 bundle 核心 API 不相容，請重新建置前端")
        assets = contract["assets"]
        if not isinstance(assets, list) or len(assets) != 3 or any(not isinstance(asset, dict) for asset in assets):
            raise IncompatiblePackage("前端契約必須包含完整 bundle 清單")
        if {asset["path"] for asset in assets} != {manifest["frontend_entry"], "app.js", "app.css"}:
            raise IncompatiblePackage("前端契約缺少必要 bundle")
        for asset in assets:
            path = contained_path(frontend, asset["path"])
            if not path.stat().st_size or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
                raise IncompatiblePackage("前端 bundle 與契約雜湊不一致，請重新建置")
        database = manifest["database"]
        if (not re.fullmatch(r"[A-Za-z0-9_.-]+\.sqlite3", database["filename"])
                or any(type(database[k]) is not int for k in ("min_schema", "max_schema", "target_schema"))
                or not 0 <= database["min_schema"] <= database["target_schema"] <= database["max_schema"]):
            raise IncompatiblePackage("Invalid database compatibility declaration")
        if not ((root / "dev-version.json").is_file() and (root / "backend/pubtool_core").is_dir()):
            verify_application_payload(root, manifest)
        return manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IncompatiblePackage(f"無效或缺少 app-manifest.json：{exc}") from exc


def verify_application_payload(root: Path, manifest):
    """Release inventory verifies consistency, not publisher identity/signatures."""
    release = json.loads((root / "release-manifest.json").read_text("utf-8-sig"))
    if (not isinstance(release, dict) or release.get("bootstrap_api_version") != 1
            or release.get("package_version") != manifest["package_version"]):
        raise IncompatiblePackage("功能包與 release manifest 版本不一致")
    files = release.get("files")
    if not isinstance(files, list) or not files:
        raise IncompatiblePackage("功能包缺少完整性清單")
    seen = set()
    for entry in files:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or entry["path"] in seen or type(entry.get("bytes")) is not int or entry["bytes"] < 0):
            raise IncompatiblePackage("功能包清單欄位無效或重複")
        path = contained_path(root, entry["path"])
        if path.stat().st_size != entry["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            raise IncompatiblePackage(f"功能包檔案完整性檢查未通過：{entry['path']}")
        seen.add(entry["path"])
    required = {"app-manifest.json", manifest["frontend_root"] + "/ui-contract.json"}
    if not required <= seen:
        raise IncompatiblePackage("功能包清單缺少入口／前端契約")
    # Git updates remove tracked obsolete files; this also rejects stray source
    # files left in runtime directories instead of importing mixed versions.
    for relative in (manifest["backend_root"], manifest["frontend_root"], "database"):
        for path in (root / relative).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                if path.relative_to(root).as_posix() not in seen:
                    raise IncompatiblePackage("功能包出現未列入清單的檔案")


def check_data_compatibility(data_dir: Path, manifest):
    path = data_dir / manifest["database"]["filename"]
    version = 0
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
    requirement = manifest["database"]
    if not requirement["min_schema"] <= version <= requirement["max_schema"]:
        raise IncompatiblePackage(f"資料格式 {version} 不在功能包支援範圍內，未載入或遷移資料")
    return version


def load_package(root: Path, manifest, context, router):
    backend = contained_path(root, manifest["backend_root"], directory=True)
    sys.path.insert(0, str(backend))
    module_name, function_name = manifest["entrypoint"].split(":")
    module = importlib.import_module(module_name)
    if not Path(module.__file__).resolve().is_relative_to(backend):
        raise IncompatiblePackage("功能模組來自其他版本；需要完整重啟")
    factory = getattr(module, function_name)
    package = factory(context, PackageRegistrar(router))
    if any(not callable(getattr(package, name, None)) for name in
           ("prepare_request", "attach", "start_frontend", "cancel_close", "before_close", "shutdown", "health_check")):
        raise IncompatiblePackage("功能包未實作核心 API 1")
    return package
