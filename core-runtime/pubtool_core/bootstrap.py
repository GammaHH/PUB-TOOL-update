"""Core startup. Package imports happen only after compatibility preflight."""
from __future__ import annotations

import json
import sys
from importlib.metadata import version
from pathlib import Path

from . import CORE_VERSION, CORE_API_VERSION, BOOTSTRAP_API_VERSION, CAPABILITIES
from .api import RuntimeContext
from .installation import install_bundled_core, verify_core
from .loader import read_manifest, check_data_compatibility
from .paths import application_data_dir
from .rendering import configure_webengine


def launch(root: Path) -> int:
    root = root.resolve()
    arguments = list(sys.argv)
    boundary = arguments.index("--webEngineArgs") if "--webEngineArgs" in arguments else len(arguments)
    developer = "--debug" in arguments[1:boundary]
    hot_reload = developer and "--hot-reload" in arguments[1:boundary]
    arguments = [argument for i, argument in enumerate(arguments) if not (i < boundary and argument in {"--debug", "--hot-reload"})]
    source_checkout = (root / "dev-version.json").is_file() and (root / "backend/pubtool_core").is_dir()
    core_root = Path(__file__).resolve().parents[1]
    if source_checkout:
        core = json.loads((root / "core-manifest.json").read_text("utf-8-sig"))
    else:
        core, _ = verify_core(core_root)
    if core["core_version"] != CORE_VERSION or core["core_api_version"] != CORE_API_VERSION:
        raise ValueError("核心程式與核心 manifest 版本不一致")
    if core["bootstrap_api_version"] != BOOTSTRAP_API_VERSION or set(core["capabilities"]) != CAPABILITIES:
        raise ValueError("核心能力清單與核心程式不一致")
    environment = core["environment"]
    if f"{sys.version_info.major}.{sys.version_info.minor}" != environment["python"]:
        raise ValueError(f"核心需要 Python {environment['python']}，請更新執行環境")
    for name, expected in environment["packages"].items():
        if version(name) != expected:
            raise ValueError(f"核心需要 {name}=={expected}，請使用 requirements-core.txt 的鎖定環境")
    manifest = read_manifest(root)
    data_dir = application_data_dir()
    check_data_compatibility(data_dir, manifest)
    if not source_checkout and core_root == root / "core-runtime":
        install_bundled_core(core_root, data_dir)
    context = RuntimeContext(root=root, data_dir=data_dir, package_version=manifest["package_version"],
                             package_id=manifest["package_id"], developer_mode=developer,
                             hot_reload=hot_reload, core_dir=core_root)
    configure_webengine(data_dir)
    from .host import run_host
    return run_host(context, manifest, arguments)
