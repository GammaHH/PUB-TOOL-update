"""Stable, Qt-free core capabilities used by RPC and package adapters."""
import os
import sys
from importlib.metadata import version, PackageNotFoundError

from . import CAPABILITIES
from .rendering import rendering_preferences, set_rendering_preferences


def runtime_info():
    try:
        pyside = version("PySide6")
    except PackageNotFoundError:
        pyside = None
    return {"python": sys.version.split()[0], "executable": sys.executable, "pyside": pyside}


def update_preferences(payload):
    path = os.getenv("PYAPPIFY_APP_JSON_PATH")
    if not path:
        return {"available": False, "reason": "直接啟動原始碼；Git 更新由 pyappify 發行版啟動器管理。"}
    from pyappify.app_config import AppConfigAPI
    api = AppConfigAPI(path=path, watch=False)
    method = payload.get("update_method")
    if method is not None:
        if method not in {"MANUAL_UPDATE", "AUTO_UPDATE", "AUTO_UPDATE_PRE_RELEASE"}:
            raise ValueError("不支援的更新模式")
        api.update(update_method=method)
    return {"available": True, **api.get()}


def register_core_routes(router, context):
    router.register_core("core.status", lambda _: {**context.version_fields(), "runtime": runtime_info(),
        "data_dir": str(context.data_dir), "core_dir": str(context.core_dir), "capabilities": sorted(CAPABILITIES)})
    router.register_core("core.rendering_preferences", lambda _: rendering_preferences(context.data_dir))
    router.register_core("core.set_rendering_preferences", lambda p: set_rendering_preferences(context.data_dir, p.get("render_mode")))
    router.register_core("core.update_preferences", update_preferences)
