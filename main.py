"""Source / pyappify launcher; all mutable data lives outside this checkout."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
from literature_app.desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
