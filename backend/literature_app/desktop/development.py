"""Local, explicitly confirmed developer snapshots. No shell or remote commands.

This module has no Qt dependency. Production RPCs cannot enter it, and a source
checkout marker is required in addition to an exact repository root match.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


SOURCE_PATHS = (
    ".gitignore", ".gitattributes", "README.md", "main.py", "main_debug.py",
    "dev-version.json", "pyproject.toml", "requirements-desktop.txt", "pyappify.yml",
    "backend/literature_app", "database", "docs", "tests", "tools", "packaging",
    "frontend/src", "frontend/public", "frontend/package.json", "frontend/package-lock.json",
    "frontend/tsconfig.json", "frontend/vite.config.ts", "frontend/debug.html", "frontend/dev-server.mjs",
)


def css_source(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not re.fullmatch(r"frontend/src/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.css", relative):
        raise ValueError("僅能回寫 frontend/src 內的來源 CSS")
    candidate = root / relative
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root / "frontend/src"):
        raise ValueError("CSS 來源不存在或超出來源目錄")
    if any(part.is_symlink() for part in [candidate, *candidate.parents] if part != root.parent):
        raise ValueError("CSS 來源不可經過連結")
    return candidate


def css_hashes(root: Path, paths: list) -> dict:
    if not isinstance(paths, list) or len(paths) > 64:
        raise ValueError("無效的 CSS 來源清單")
    return {name: hashlib.sha256(css_source(root, name).read_bytes()).hexdigest() for name in paths}


class DeveloperRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.git = shutil.which("git.exe") or shutil.which("git")
        self.plan = None

    def _git(self, *args: str, env=None) -> bytes:
        if not self.git:
            raise ValueError("找不到 Git，請先安裝 Git for Windows")
        # No implicit index refresh/locks for read operations. Every invocation
        # retains its own completion, stdout, stderr and exit-code evidence.
        child_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        child_env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
        if env:
            child_env.update(env)
        logs = self.root / "logs/development"
        logs.mkdir(parents=True, exist_ok=True)
        stem = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8]
        prefix = logs / stem
        completed, code, output, errors = False, None, b"", b""
        try:
            process = subprocess.run([self.git, "-c", "core.fsmonitor=false", *args], cwd=self.root,
                                     env=child_env, capture_output=True, timeout=60, check=False,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            completed, code = True, process.returncode
            output, errors = process.stdout, process.stderr
        except subprocess.TimeoutExpired as exc:
            output, errors = exc.stdout or b"", exc.stderr or b""
            raise ValueError("Git 超時，請查看 logs/development，勿重複點擊儲存") from exc
        finally:
            prefix.with_suffix(".stdout.log").write_bytes(output)
            prefix.with_suffix(".stderr.log").write_bytes(errors)
            prefix.with_suffix(".process.json").write_text(json.dumps({
                "arguments": list(args), "completed": completed, "exit_code": code,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        if not completed or code != 0:
            raise ValueError(f"Git 操作失敗（{code}）：{errors.decode('utf-8', 'replace').strip()}；日誌 {prefix}")
        return output

    def _text(self, *args, **kwargs):
        return self._git(*args, **kwargs).decode("utf-8", "replace").strip()

    def _guard(self):
        marker = self.root / "dev-version.json"
        if marker.is_symlink() or not marker.is_file() or json.loads(marker.read_text("utf-8")).get("schema") != 1:
            raise ValueError("此功能僅供具有 dev-version.json 的開發來源庫")
        if not (self.root / ".git").is_dir() or (self.root / ".git").resolve() != self.root / ".git":
            raise ValueError("需要獨立的本地開發 Git 庫；不在安裝版或 worktree 操作")
        if Path(self._text("rev-parse", "--show-toplevel")).resolve() != self.root:
            raise ValueError("Git 根目錄與開發來源不一致")
        if any((self.root / ".git" / name).exists() for name in
               ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")):
            raise ValueError("Git 正在合併／rebase 等操作，請先完成或取消，再使用 Debug 儲存")

    def _paths(self):
        tracked = self._git("ls-files", "-z", "--", *SOURCE_PATHS).decode("utf-8").split("\0")
        return [p for p in SOURCE_PATHS if (self.root / p).exists() or any(t == p or t.startswith(p + "/") for t in tracked)]

    def _fingerprint(self, paths):
        # Includes tracked deletions, modes and untracked source bytes. Refuse a
        # changed review rather than silently committing additional source work.
        listing = self._git("ls-files", "--stage", "-z", "--", *paths)
        names = self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *paths)
        digest = hashlib.sha256(listing)
        for raw in sorted(set(filter(None, names.split(b"\0")))):
            path = self.root / raw.decode("utf-8")
            relative_parts = path.relative_to(self.root).parts
            if (any(part.lower() in {"node_modules", "__pycache__", ".venv", "dist", "logs", "data"} for part in relative_parts)
                    or path.name.lower().startswith(".env") or re.search(r"\.(?:sqlite3?|db)(?:[-.].*)?$", path.name, re.I)
                    or path.suffix.lower() in {".pyc", ".pyo", ".log"}):
                raise ValueError(f"來源白名單內出現資料／環境／產物檔，拒絕提交：{path.relative_to(self.root)}")
            if path.is_symlink() or not path.resolve().is_relative_to(self.root) or any(p.is_symlink() for p in path.parents if p.is_relative_to(self.root)):
                raise ValueError("來源白名單內有連結，請先移除連結再儲存")
            digest.update(raw + b"\0")
            digest.update(path.read_bytes() if path.is_file() else b"<deleted>")
        return digest.hexdigest()

    def status(self):
        self._guard()
        return {"name": json.loads((self.root / "dev-version.json").read_text("utf-8"))["name"],
                "head": self._text("rev-parse", "HEAD"), "branch": self._text("symbolic-ref", "--short", "HEAD"),
                "changes": self._text("status", "--short", "--untracked-files=all", "--", *self._paths())}

    def _no_staged(self):
        if self._git("diff", "--cached", "--name-only", "-z"):
            raise ValueError("Git 暫存區已有變更，請先自行處理；PUSH 不會包含或覆蓋它們")

    def prepare(self, name, edits):
        self._guard()
        self._no_staged()
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ValueError("版本名稱需為 1–80 字，不能有換行或控制字元")
        if not isinstance(edits, list) or len(edits) > 32:
            raise ValueError("CSS 修改清單無效")
        files, previews = {}, []
        for edit in edits:
            if not isinstance(edit, dict):
                raise ValueError("CSS 修改無效")
            relative, text = edit.get("path"), edit.get("text")
            path = css_source(self.root, relative)
            before = path.read_bytes()
            if hashlib.sha256(before).hexdigest() != edit.get("expected_sha256"):
                raise ValueError(f"{relative} 的來源已改變，請先重載樣式再預覽")
            if not isinstance(text, str) or len(text.encode("utf-8")) > 1_000_000:
                raise ValueError("CSS 修改過大或無效")
            # Vite rewrites URLs/imports. Do not write transformed asset paths
            # into source; edit that stylesheet in the source editor instead.
            if re.search(r"url\s*\(|@import", text + before.decode("utf-8"), re.I):
                raise ValueError(f"{relative} 含 url／@import，請直接修改來源檔，不從 F12 回寫")
            if relative in files:
                raise ValueError("CSS 修改重複")
            files[relative] = text.encode("utf-8")
            previews.extend(difflib.unified_diff(before.decode("utf-8").splitlines(True), text.splitlines(True),
                                                 fromfile=relative, tofile=relative + " (F12)"))
        files["dev-version.json"] = (json.dumps({"schema": 1, "name": name.strip()}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        paths = self._paths()
        head, ref = self._text("rev-parse", "HEAD"), self._text("symbolic-ref", "HEAD")
        fingerprint = self._fingerprint(paths)
        preview = self._text("status", "--short", "--untracked-files=all", "--", *paths)
        diff = self._text("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", *paths)
        untracked_diff = []
        for raw in filter(None, self._git("ls-files", "--others", "--exclude-standard", "-z", "--", *paths).split(b"\0")):
            relative = raw.decode("utf-8")
            content = (self.root / relative).read_bytes()
            if b"\0" in content or len(content) > 1_000_000:
                untracked_diff.append(f"\n新增檔 {relative}: {len(content)} bytes; SHA256 {hashlib.sha256(content).hexdigest()}\n")
            else:
                untracked_diff.extend(difflib.unified_diff([], content.decode("utf-8", "replace").splitlines(True), fromfile="/dev/null", tofile=relative))
        if fingerprint != self._fingerprint(paths) or head != self._text("rev-parse", "HEAD"):
            raise ValueError("預覽期間來源有修改，請重新預覽")
        token = uuid.uuid4().hex
        self.plan = {"token": token, "name": name.strip(), "head": head, "ref": ref, "files": files,
                     "fingerprint": fingerprint, "paths": paths, "expires": time.monotonic() + 300}
        return {"token": token, "head": head, "preview":
                f"版本：{name.strip()}\n本地 Git commit；不 push 遠端、不發布 UPDATE。\n"
                "只收錄來源白名單；資料庫／logs／dist／無關檔案不納入。\n"
                "F12 回寫會標準化整份自有 CSS（原有註解／排版可由 Git 歷史找回）。\n\n"
                "DOM、element.style 與 Console 不會回寫來源；提交成功後會在草稿安全時重新載入畫面。\n\n"
                + preview + "\n\n" + diff + "\n\n" + "".join(untracked_diff) + "\n\n" + "".join(previews)
                + "\n\n版本名稱將寫入 dev-version.json：" + json.dumps({"schema": 1, "name": name.strip()}, ensure_ascii=False)}

    def commit(self, token):
        self._guard()
        plan = self.plan
        self.plan = None  # one confirmation, one attempt; no automatic retries
        if not plan or plan["token"] != token or time.monotonic() > plan["expires"]:
            raise ValueError("儲存預覽已失效，請重新按 PUSH")
        git_dir = self.root / ".git"
        lock = git_dir / "index.lock"
        logs = self.root / "logs/development" / ("snapshot_" + uuid.uuid4().hex)
        logs.mkdir(parents=True)
        temporary_index = logs / "index"
        owned_lock, updated_ref = False, False
        new_head = None
        try:
            with lock.open("xb") as index_lock:
                owned_lock = True
                self._no_staged()
                if self._text("rev-parse", "HEAD") != plan["head"] or self._text("symbolic-ref", "HEAD") != plan["ref"]:
                    raise ValueError("Git 版本或分支已改變，請重新預覽")
                if self._fingerprint(plan["paths"]) != plan["fingerprint"]:
                    raise ValueError("來源在確認期間已改變，請重新預覽")
                env = {"GIT_INDEX_FILE": str(temporary_index)}
                self._git("read-tree", plan["head"], env=env)
                for relative, content in plan["files"].items():
                    path = self.root / relative
                    backup = logs / "before" / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    backup.write_bytes(path.read_bytes())
                    # Same-volume replace; backups remain even on Git failure.
                    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
                    try:
                        with temporary.open("xb") as output:
                            output.write(content)
                        temporary.replace(path)
                    finally:
                        temporary.unlink(missing_ok=True)
                written_fingerprint = self._fingerprint(plan["paths"])
                self._git("add", "-A", "--", *plan["paths"], env=env)
                if written_fingerprint != self._fingerprint(plan["paths"]):
                    raise ValueError("建立快照期間來源有修改，未提交；回寫備份在 logs/development")
                tree = self._text("write-tree", env=env)
                if tree == self._text("rev-parse", plan["head"] + "^{tree}"):
                    return {"saved": False, "head": plan["head"], "name": plan["name"], "message": "沒有新的來源變更"}
                new_head = self._text("commit-tree", tree, "-p", plan["head"], "-m", "PUB-TOOL DEBUG: " + plan["name"], env=env)
                index_lock.write(temporary_index.read_bytes())
                index_lock.flush()
                os.fsync(index_lock.fileno())
                self._git("update-ref", "-m", "PUB-TOOL DEBUG snapshot", plan["ref"], new_head, plan["head"])
                updated_ref = True
            os.replace(lock, git_dir / "index")
            owned_lock = False
            return {"saved": True, "head": new_head, "name": plan["name"], "message": "已儲存本地 Git；未發布 UPDATE"}
        except FileExistsError as exc:
            raise ValueError("Git index.lock 已存在，請等待其他 Git 操作結束；不會自動刪除鎖") from exc
        except Exception:
            if updated_ref:
                # CAS rollback only our own ref change. Never reset user work.
                self._git("update-ref", plan["ref"], plan["head"], new_head)
            raise
        finally:
            if owned_lock:
                lock.unlink(missing_ok=True)
            temporary_index.unlink(missing_ok=True)
