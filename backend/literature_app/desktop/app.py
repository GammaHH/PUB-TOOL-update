from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from ..workspace.store import RevisionConflict, WorkspaceStore, application_data_dir
from .rendering import configure_webengine
from .development import css_hashes

configure_webengine(application_data_dir())

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot, QMimeData, Qt
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import QApplication, QDockWidget, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView

from .service import DesktopService, pubmed_url
from .webchannel import webchannel_bootstrap_script


class JobSignals(QObject):
    finished = Signal(str)


class Job(QRunnable):
    def __init__(self, service, envelope):
        super().__init__()
        self.service, self.envelope = service, envelope
        self.signals = JobSignals()

    def run(self):
        ident = self.envelope.get("id")
        try:
            result = self.service.call(self.envelope["action"], self.envelope.get("payload", {}))
            response = {"id": ident, "ok": True, "result": result}
        except Exception as exc:
            response = {"id": ident, "ok": False, "error": str(exc),
                        "code": "REVISION_CONFLICT" if isinstance(exc, RevisionConflict) else "REQUEST_FAILED"}
        self.signals.finished.emit(json.dumps(response, ensure_ascii=False))


class Bridge(QObject):
    response = Signal(str)
    dirtyChanged = Signal(bool)
    reloadRequested = Signal()
    developerMessage = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.dirty = False
        self.jobs = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    @Slot(bool)
    def setDirty(self, dirty):
        self.dirty = dirty
        self.dirtyChanged.emit(dirty)

    @Slot(str, result=str)
    def developerStyles(self, raw):
        try:
            if not self.service.developer_mode:
                raise ValueError("僅供開發者模式")
            if len(raw) > 10000:
                raise ValueError("CSS 清單過大")
            return json.dumps({"hashes": css_hashes(self.service.root, json.loads(raw))})
        except (ValueError, OSError, TypeError) as exc:
            return json.dumps({"error": str(exc)})

    @Slot()
    def developerReload(self):
        if self.service.developer_mode:
            self.reloadRequested.emit()

    @Slot(str)
    def developerNotice(self, message):
        if self.service.developer_mode:
            self.developerMessage.emit(message[:2000])

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

    @Slot(str)
    def openPubMed(self, query):
        try:
            QDesktopServices.openUrl(QUrl(pubmed_url(query)))
        except ValueError:
            pass

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
            if envelope.get('action') in {'export_excel', 'export_endnote'}:
                payload = envelope.get('payload', {})
                if not isinstance(payload, dict):
                    raise ValueError('匯出參數無效')
                ris = envelope['action'] == 'export_endnote'
                suffix = '.ris' if ris else '.xlsx'
                suggested = 'PUB-TOOL_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + suffix
                filename, _ = QFileDialog.getSaveFileName(self.parent(), '匯出 EndNote RIS' if ris else '匯出文獻與摘要', suggested, 'EndNote RIS (*.ris)' if ris else 'Excel (*.xlsx)')
                if not filename:
                    self.response.emit(json.dumps({'id': envelope['id'], 'ok': True, 'result': {'cancelled': True}}))
                    return
                if not filename.lower().endswith(suffix):
                    filename += suffix
                # Always replace caller-provided target with native dialog choice.
                envelope['payload'] = {**payload, 'target': filename}
            job = Job(self.service, envelope)
            self.jobs[envelope["id"]] = job
            job.signals.finished.connect(self.complete)
            self.pool.start(job)
        except (ValueError, TypeError) as exc:
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
        self.developer_loaded = False
        self.allow_developer_reload = False

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[WebEngine JS {level.name}] {source_id}:{line_number} {message}", file=sys.stderr, flush=True)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() == "blob":
            return True  # JSON draft downloads generated by this page.
        local = url.isLocalFile() and Path(url.toLocalFile()).resolve() == self.index
        development = self.dev_origin and url.scheme() == "http" and url.host() == "127.0.0.1" and url.port() == self.dev_origin.port() and url.path() == "/debug.html"
        if not (local or development):
            return False
        window = self.parent()
        if is_main_frame and self.developer_loaded:
            if window.bridge.dirty or window.bridge.jobs:
                window.bridge.reloadRequested.emit()
                return False
            if development and not self.allow_developer_reload:
                window.bridge.reloadRequested.emit()
                return False
            self.allow_developer_reload = False
        return True


class WorkspaceView(QWebEngineView):
    """Coalesce resize events into one repaint without navigating/reloading."""
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
    def __init__(self, service, assets, hot_reload=False):
        super().__init__()
        self.setWindowTitle("PUB-TOOL · Literature Workspace")
        self.resize(1500, 950)
        self.bridge = Bridge(service, self)
        self.view = WorkspaceView(self)
        self.profile = QWebEngineProfile(self)  # off-the-record; no shared browser state
        self.interceptor = LocalAssetsOnly(assets, self)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page = LocalPage(self.profile, assets / "index.html", self)
        self.view.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("desktop", self.bridge)
        self.page.setWebChannel(self.channel, 0)  # MainWorld, same as the frontend
        self.page.scripts().insert(webchannel_bootstrap_script())
        self.page.loadFinished.connect(self.frontend_loaded)
        self.profile.downloadRequested.connect(self.download)
        self.setCentralWidget(self.view)
        self.developer_controls = None
        if service.developer_mode:
            self.setWindowTitle("PUB-TOOL · DEBUG · Literature Workspace")
            self.devtools = QWebEngineView(self)
            self.devtools_dock = QDockWidget("介面檢查工具 · 來源 CSS 可 PUSH／DOM 與 Console 臨時預覽", self)
            self.devtools_dock.setObjectName("pub-tool-devtools")
            self.devtools_dock.setWidget(self.devtools)
            self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.devtools_dock)
            self.page.setDevToolsPage(self.devtools.page())
            self.devtools_dock.hide()
            action = self.devtools_dock.toggleViewAction()
            action.setText("介面檢查工具（F12）")
            action.setShortcut("F12")
            from .developer_ui import DeveloperControls
            self.developer_controls = DeveloperControls(self, service, hot_reload, action)
        if hot_reload and self.developer_controls:
            self.developer_controls.start_server()
        else:
            self.view.load(QUrl.fromLocalFile(str((assets / "index.html").resolve())))

    def frontend_loaded(self, ok):
        print(f"[frontend] load_finished={ok} url={self.page.url().toString()}", file=sys.stderr, flush=True)
        if ok:
            self.page.runJavaScript(
                "JSON.stringify({transport:!!window.qt?.webChannelTransport,webchannel:typeof window.QWebChannel})",
                lambda result: print(f"[frontend] bridge={result}", file=sys.stderr, flush=True),
            )

    def download(self, download):
        filename, _ = QFileDialog.getSaveFileName(self, "匯出 JSON 草稿或樣式設定", download.suggestedFileName(), "JSON (*.json)")
        if filename:
            path = Path(filename)
            download.setDownloadDirectory(str(path.parent))
            download.setDownloadFileName(path.name)
            download.accept()
        else:
            download.cancel()

    def closeEvent(self, event):
        if self.bridge.jobs:
            if self.developer_controls:
                self.developer_controls.close_allowed = False
            QMessageBox.information(self, "操作進行中", "請等待目前操作完成後再關閉。")
            event.ignore()
        elif self.bridge.dirty:
            if self.developer_controls:
                self.developer_controls.close_allowed = False
            QMessageBox.information(self, "尚未儲存", "請先儲存或在介面按「還原」處理草稿，再關閉。")
            event.ignore()
        else:
            if self.developer_controls:
                if not self.developer_controls.close_allowed:
                    event.ignore()
                    self.developer_controls.request_close()
                    return
                self.developer_controls.stop_server()
                if self.developer_controls.restart_requested:
                    QApplication.instance().setQuitOnLastWindowClosed(False)
                    QApplication.exit(75)
            event.accept()


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    assets = root / "frontend" / "dist"
    qt_args = list(sys.argv)
    app_args_end = qt_args.index("--webEngineArgs") if "--webEngineArgs" in qt_args else len(qt_args)
    developer_mode = "--debug" in qt_args[1:app_args_end]
    hot_reload = developer_mode and "--hot-reload" in qt_args[1:app_args_end]
    if "--hot-reload" in qt_args[1:app_args_end]:
        qt_args.remove("--hot-reload")
    if developer_mode:
        qt_args.remove("--debug")
    app = QApplication(qt_args)
    app.setApplicationName("PUB-TOOL")
    app.setOrganizationName("PUB-TOOL")
    if not (assets / "index.html").is_file():
        QMessageBox.critical(None, "尚未建置前端", "請先執行 tools/Build-Frontend.ps1，詳見 docs/desktop-app.md。")
        return 1
    store = WorkspaceStore(application_data_dir() / "literature.sqlite3")
    window = MainWindow(DesktopService(store, root, developer_mode=developer_mode), assets, hot_reload=hot_reload)
    if window.developer_controls:
        app.aboutToQuit.connect(window.developer_controls.stop_server)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
