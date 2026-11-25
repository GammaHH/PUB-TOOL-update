"""Startup display preferences; no Qt imports or database writes."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def rendering_preferences(data_dir: Path) -> dict:
    path = data_dir / "desktop-settings.json"
    mode, warning = "auto", None
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mode = payload["render_mode"]
            if mode not in {"auto", "software"}:
                raise ValueError("Unsupported render mode")
        except (OSError, ValueError, KeyError, TypeError):
            mode, warning = "auto", "顯示設定無法讀取，目前使用自動模式，請重新儲存設定。"
    return {"render_mode": mode, "warning": warning,
            "gpu_disabled_this_process": "--disable-gpu" in os.getenv("QTWEBENGINE_CHROMIUM_FLAGS", "").split() or "--disable-gpu" in sys.argv}


def set_rendering_preferences(data_dir: Path, mode: str) -> dict:
    if mode not in ("auto", "software"):
        raise ValueError("不支援的繪圖模式")
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    temporary = data_dir / f"desktop-settings.{stamp}.{os.getpid()}.tmp"
    with temporary.open("x", encoding="utf-8") as output:
        json.dump({"render_mode": mode}, output)
    temporary.replace(data_dir / "desktop-settings.json")
    return rendering_preferences(data_dir)


def configure_webengine(data_dir: Path) -> None:
    # Must be called before importing Qt WebEngine or creating QApplication.
    if rendering_preferences(data_dir)["render_mode"] == "software":
        flags = os.getenv("QTWEBENGINE_CHROMIUM_FLAGS", "")
        if "--disable-gpu" not in flags.split():
            os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-gpu").strip()
