"""Qt-free API for packages. Only the host imports Qt."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import CORE_VERSION, CORE_API_VERSION, CAPABILITIES


@dataclass(frozen=True)
class RuntimeContext:
    root: Path
    data_dir: Path
    package_version: str
    package_id: str = "PUB-TOOL"
    developer_mode: bool = False
    hot_reload: bool = False
    core_version: str = CORE_VERSION
    core_api_version: int = CORE_API_VERSION
    core_dir: Path | None = None

    def version_fields(self):
        return {"core_version": self.core_version, "core_api_version": self.core_api_version,
                "package_version": self.package_version, "version": self.package_version,
                "core_capabilities": sorted(CAPABILITIES)}


@dataclass(frozen=True)
class PreparedRequest:
    payload: dict
    cancelled: bool = False


@dataclass(frozen=True)
class CloseDecision:
    allow: bool
    exit_code: int = 0


class NativeServices(Protocol):
    def choose_save_file(self, title: str, suggested: str, file_filter: str) -> str | None: ...
    def open_url(self, url: str) -> None: ...
    def reload(self) -> None: ...
    def notice(self, message: str) -> None: ...


class Router:
    """Packages register app routes; reserved core routes cannot be replaced."""
    def __init__(self):
        self._routes: dict[str, Callable] = {}
        self._native: dict[str, Callable] = {}

    def register_core(self, name: str, handler: Callable):
        if not name.startswith("core.") or name in self._routes:
            raise ValueError("Invalid/duplicate core route")
        self._routes[name] = handler

    def register_app(self, name: str, handler: Callable, *, legacy=True):
        if not isinstance(name, str) or not name or "." in name:
            raise ValueError("Package route must be an unqualified app action")
        keys = ["app." + name, *([name] if legacy else [])]
        if any(key in self._routes for key in keys):
            raise ValueError("Duplicate package route")
        for key in keys:
            self._routes[key] = handler

    def register_native(self, name: str, handler: Callable):
        if not name.startswith("app.") or name in self._native:
            raise ValueError("Native package routes require app.* and cannot be replaced")
        self._native[name] = handler

    def call(self, action, payload):
        if not isinstance(payload, dict) or not isinstance(action, str) or action not in self._routes:
            raise ValueError("不支援的操作或請求內容")
        return self._routes[action](payload)

    def call_native(self, action, payload, native):
        if not isinstance(payload, dict) or action not in self._native:
            raise ValueError("不支援的原生接口")
        return self._native[action](payload, native)

    def contains(self, action):
        return isinstance(action, str) and action in self._routes

    @property
    def actions(self):
        return sorted(self._routes)


class PackageRegistrar:
    """Public registration surface; packages do not receive core registration."""
    def __init__(self, router):
        self._router = router

    def register_app(self, name, handler, *, legacy=True):
        self._router.register_app(name, handler, legacy=legacy)

    def register_native(self, name, handler):
        self._router.register_native(name, handler)
