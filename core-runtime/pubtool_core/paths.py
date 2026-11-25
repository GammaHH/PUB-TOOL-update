"""User state is independent of the current package checkout."""
import os
import sys
from pathlib import Path


def application_data_dir() -> Path:
    override = os.getenv("PUB_TOOL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "PUB-TOOL"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/PUB-TOOL"
    return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "pub-tool"
