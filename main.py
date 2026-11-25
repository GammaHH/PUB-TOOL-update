"""Stable bootstrap API 1. Application updates use the separately installed core."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


def _data_dir():
    if os.getenv("PUB_TOOL_DATA_DIR"):
        return Path(os.environ["PUB_TOOL_DATA_DIR"]).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "PUB-TOOL"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/PUB-TOOL"
    return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "pub-tool"


def find_core(root):
    if (root / "dev-version.json").is_file() and (root / "backend/pubtool_core/bootstrap.py").is_file():
        return root / "backend"
    if os.getenv("PUB_TOOL_CORE_DIR"):
        candidate = Path(os.environ["PUB_TOOL_CORE_DIR"]).expanduser().resolve()
        if not (candidate / "pubtool_core/bootstrap.py").is_file():
            raise RuntimeError("PUB_TOOL_CORE_DIR 沒有有效核心")
        return candidate
    bundled = root / "core-runtime"
    if (bundled / "pubtool_core/bootstrap.py").is_file():
        return bundled
    app = json.loads((root / "app-manifest.json").read_text("utf-8-sig"))
    requirement = app["requires_core"]
    minimum = tuple(int(x) for x in requirement["min_version"].split("-", 1)[0].split("."))
    candidates = []
    cores = _data_dir() / "cores"
    for candidate in cores.iterdir() if cores.is_dir() else []:
        if not candidate.is_dir() or candidate.is_symlink() or not re.fullmatch(r"\d+\.\d+\.\d+", candidate.name):
            continue
        try:
            core = json.loads((candidate / "core-manifest.json").read_text("utf-8-sig"))
            numbers = tuple(int(x) for x in core["core_version"].split("."))
            if (requirement["api_min"] <= core["core_api_version"] <= requirement["api_max"] and numbers >= minimum
                    and set(requirement["capabilities"]) <= set(core["capabilities"])):
                candidates.append((numbers, candidate))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if candidates:
        return max(candidates, key=lambda pair: pair[0])[1]
    raise RuntimeError("缺少相容的 PUB-TOOL 核心；請先安裝包含 core-runtime 的轉換版，再套用功能更新包")


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(find_core(root)))
    from pubtool_core.bootstrap import launch
    return launch(root)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PUB-TOOL startup failed: {exc}", file=sys.stderr, flush=True)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "PUB-TOOL 啟動未完成", 0x10)
        raise SystemExit(1)
