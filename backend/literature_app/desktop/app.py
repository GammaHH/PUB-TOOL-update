"""Compatibility entrypoint; the Qt host now lives in pubtool_core."""
from pathlib import Path


def main():
    from pubtool_core.bootstrap import launch
    return launch(Path(__file__).resolve().parents[3])


if __name__ == "__main__":
    raise SystemExit(main())
