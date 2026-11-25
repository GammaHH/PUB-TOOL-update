"""Qt host. Research services enter only through a manifest-selected package."""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot, QMimeData, Qt
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView

from .api import Router, PreparedRequest
from .loader import contained_path, load_package, check_data_compatibility
from .operations import register_core_routes
from .webchannel import webchannel_bootstrap_script


class JobSignals(QObject):
    finished = Signal(str)


class Job(QRunnable):
    def __init__(self, router, envelope):
        super().__init__()
        self.router, self.envelope = router, envelope
        self.signals = JobSignals()

    def run(self):
        try:
            result = self.router.call(self.envelope["action"], self.envelope.get("payload", {}))
            response = {"id": self.envelope["id"], "ok": True, "result": result}
        except Exception as exc:
            response = {"id": self.envelope["id"], "ok": False, "error": str(exc),
                        "code": getattr(exc, "rpc_code", "REQUEST_FAILED")}
        self.signals.finished.emit(json.dumps(response, ensure_ascii=False))


class Native:
    def __init__(self, window):
        self.window = window

    def choose_save_file(self, title, suggested, file_filter):
        name, _ = QFileDialog.getSaveFileName(self.window, title, suggested, file_filter)
        return name or None

    def open_url(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("只允許 HTTP／HTTPS 外部連結")
        QDesktopServices.openUrl(QUrl(url))

    def reload(self):
        self.window.bridge.reloadRequested.emit()

    def notice(self, message):
        print(f"[core] {str(message)[:2000]}", file=sys.stderr, flush=True)
        self.window.bridge.coreMessage.emit(str(message)[:2000])


class Bridge(QObject):
    response = Signal(str)
    dirtyChanged = Signal(bool)
    reloadRequested = Signal()
    coreMessage = Signal(str)

    def __init__(self, window, router, package):
        super().__init__(window)
        self.window, self.router, self.package = window, router, package
        self.native = Native(window)
        self.dirty = False
        self.jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    @Slot(bool)
    def setDirty(self, dirty):
        self.dirty = dirty
        self.dirtyChanged.emit(dirty)

    @Slot(str, result=bool)
    def copyHtml(self, html):
        if len(html) > 20_000_000:
            return False
        mime = QMimeData()
        mime.setHtml(html)
        document = QTextDocument()
        document.setHtml(html)
        mime.setText(document.toPlainText())
        QApplication.clipboard().setMimeData(mime)
        return True

    @Slot(str, str, result=str)
    def nativeCall(self, action, raw):
        try:
            if len(raw) > 250000:
                raise ValueError("原生接口請求過大")
            payload = json.loads(raw)
            if action == "core.frontend_ready":
                result = self.window.mark_ready(payload)
            else:
                result = self.router.call_native(action, payload, self.native)
            return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    @Slot(str, str)
    def nativeNotify(self, action, raw):
        result = json.loads(self.nativeCall(action, raw))
        if not result["ok"]:
            self.native.notice(result["error"])

    @Slot(str)
    def request(self, raw):
        envelope = None
        try:
            if len(raw) > 2_500_000:
                raise ValueError("請求過大")
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("id"), str) or len(envelope["id"]) > 100:
                raise ValueError("無效請求")
            if envelope["id"] in self.jobs or len(self.jobs) >= 8:
                raise ValueError("請等待目前操作完成")
            payload = envelope.get("payload", {})
            if not isinstance(payload, dict) or not self.router.contains(envelope.get("action")):
                raise ValueError("不支援的操作或參數")
            prepared = (PreparedRequest(payload) if envelope["action"].startswith("core.")
                        else self.package.prepare_request(envelope["action"], payload, self.native))
            if prepared.cancelled:
                self.response.emit(json.dumps({"id": envelope["id"], "ok": True, "result": {"cancelled": True}}))
                return
            envelope["payload"] = prepared.payload
            job = Job(self.router, envelope)
            self.jobs[envelope["id"]] = job
            job.signals.finished.connect(self.complete)
            self.pool.start(job)
        except Exception as exc:
            ident = envelope.get("id") if isinstance(envelope, dict) else None
            self.response.emit(json.dumps({"id": ident, "ok": False, "error": str(exc)}))

    @Slot(str)
    def complete(self, raw):
        self.jobs.pop(json.loads(raw)["id"], None)
        self.response.emit(raw)


class LocalAssetsOnly(QWebEngineUrlRequestInterceptor):
    def __init__(self, assets, parent=None):
        super().__init__(parent)
        self.assets = assets.resolve()
        self.dev_origin = None

    def interceptRequest(self, info):
        url = info.requestUrl()
        if self.dev_origin and url.scheme() in {"http", "ws"} and url.host() == "127.0.0.1" and url.port() == self.dev_origin.port():
            return
        if url.scheme() in {"data", "blob", "qrc"}:
            return
        if url.isLocalFile() and Path(url.toLocalFile()).resolve().is_relative_to(self.assets):
            return
        info.block(True)


class LocalPage(QWebEnginePage):
    def __init__(self, profile, index, parent):
        super().__init__(profile, parent)
        self.index = index.resolve()
        self.dev_origin = None
        self.development_entry = "/debug.html"
        self.developer_loaded = False
        self.allow_developer_reload = False

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[WebEngine JS {level.name}] {source_id}:{line_number} {message}", file=sys.stderr, flush=True)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "blob":
            return True
        local = url.isLocalFile() and Path(url.toLocalFile()).resolve() == self.index
        development = self.dev_origin and url.scheme() == "http" and url.host() == "127.0.0.1" and url.port() == self.dev_origin.port() and url.path() == self.development_entry
        if not (local or development):
            return False
        if is_main_frame and self.developer_loaded:
            bridge = self.parent().bridge
            if bridge.dirty or bridge.jobs or (development and not self.allow_developer_reload):
                bridge.reloadRequested.emit()
                return False
            self.allow_developer_reload = False
        return True


class WorkspaceView(QWebEngineView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.repaint_timer = QTimer(self)
        self.repaint_timer.setSingleShot(True)
        self.repaint_timer.setInterval(100)
        self.repaint_timer.timeout.connect(self.refresh_surface)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "repaint_timer"):
            self.repaint_timer.start()

    def refresh_surface(self):
        self.update()
        surface = self.focusProxy()
        if surface is not None:
            surface.update()


class MainWindow(QMainWindow):
    def __init__(self, context, manifest, router, package):
        super().__init__()
        self.context, self.manifest, self.package = context, manifest, package
        self.ready = False
        self.session_id = uuid.uuid4().hex
        self.setWindowTitle(package.title)
        self.resize(1500, 950)
        assets = contained_path(context.root, manifest["frontend_root"], directory=True)
        self.bridge = Bridge(self, router, package)
        self.view = WorkspaceView(self)
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalAssetsOnly(assets, self)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page = LocalPage(self.profile, assets / manifest["frontend_entry"], self)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("desktop", self.bridge)
        self.page.setWebChannel(self.channel, 0)
        self.page.scripts().insert(webchannel_bootstrap_script())
        self.page.loadFinished.connect(self.frontend_loaded)
        self.profile.downloadRequested.connect(self.download)
        self.setCentralWidget(self.view)
        package.attach(self)
        if not package.start_frontend(self):
            self.view.load(QUrl.fromLocalFile(str((assets / manifest["frontend_entry"]).resolve())))

    def frontend_loaded(self, ok):
        print(f"[frontend] load_finished={ok} url={self.page.url().toString()}", file=sys.stderr, flush=True)
        if ok:
            self.page.developer_loaded = True
            self.page.runJavaScript("JSON.stringify({transport:!!window.qt?.webChannelTransport,webchannel:typeof window.QWebChannel})",
                lambda result: print(f"[frontend] bridge={result}", file=sys.stderr, flush=True))

    def mark_ready(self, payload):
        if not isinstance(payload, dict) or payload.get("package_version") != self.context.package_version:
            raise ValueError("前端握手版本與功能包不一致")
        if not self.ready:
            health = self.package.health_check()
            if check_data_compatibility(self.context.data_dir, self.manifest) != self.manifest["database"]["target_schema"]:
                raise ValueError("功能包尚未達成宣告的資料 schema")
            directory = self.context.data_dir / "sessions"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (self.session_id + ".json")
            with path.open("x", encoding="utf-8") as output:
                json.dump({**self.context.version_fields(), "healthy": True, "pid": os.getpid(),
                           "checked_utc": datetime.now(timezone.utc).isoformat(), **health}, output, indent=2)
            self.ready = True
        return {"healthy": True, **self.context.version_fields()}

    def download(self, download):
        title, file_filter = self.package.download_options
        filename = self.bridge.native.choose_save_file(title, download.suggestedFileName(), file_filter)
        if filename:
            path = Path(filename)
            download.setDownloadDirectory(str(path.parent))
            download.setDownloadFileName(path.name)
            download.accept()
        else:
            download.cancel()

    def closeEvent(self, event):
        if self.bridge.jobs or self.bridge.dirty:
            self.package.cancel_close()
            QMessageBox.information(self, "尚有工作", "請先儲存或捨棄草稿，並等待目前操作完成後再關閉。")
            event.ignore()
            return
        decision = self.package.before_close()
        if not decision.allow:
            event.ignore()
            return
        if decision.exit_code not in ({0, 75} if self.context.hot_reload else {0}):
            raise ValueError("功能包要求不允許的離開代碼")
        self.package.shutdown()
        if decision.exit_code:
            QApplication.instance().setQuitOnLastWindowClosed(False)
            QApplication.exit(decision.exit_code)
        event.accept()


def run_host(context, manifest, arguments):
    app = QApplication(arguments)
    app.setApplicationName(context.package_id)
    app.setOrganizationName(context.package_id)
    router = Router()
    register_core_routes(router, context)
    package = load_package(context.root, manifest, context, router)
    window = MainWindow(context, manifest, router, package)
    app.aboutToQuit.connect(package.shutdown)
    window.show()
    return app.exec()
