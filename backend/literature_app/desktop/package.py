"""PUB-TOOL feature entrypoint. Registers domain routes on stable core API 1."""
from datetime import datetime

from pubtool_core.api import CloseDecision, PreparedRequest
from ..workspace.store import WorkspaceStore
from .development import css_hashes
from .service import DesktopService, pubmed_url


APP_ACTIONS = (
    "status", "projects", "create_project", "load", "save", "history", "history_preferences",
    "set_history_preferences", "delete_revision", "validate_graph", "compile", "compile_with_status", "resolve",
    "pubmed_search", "pubmed_runs", "pubmed_articles", "pubmed_fetch", "screen_article", "reading_html",
    "export_excel", "export_endnote", "style_settings", "validate_style_settings", "save_style_settings",
    "rendering_preferences", "set_rendering_preferences", "update_preferences", "local_model_settings",
    "save_local_model_settings", "local_model_models", "local_model_test", "debug_validate",
    "debug_git_status", "debug_git_prepare", "debug_git_commit",
    "preview_project_import", "import_project", "export_project",
    "trashed_projects", "trash_project", "restore_project",
    "screening_history", "undo_screening",
)


class LiteraturePackage:
    download_options = ("匯出 JSON 草稿或樣式設定", "JSON (*.json)")

    def __init__(self, context, router):
        self.context, self.router = context, router
        self.store = WorkspaceStore(context.data_dir / "literature.sqlite3")
        self.service = DesktopService(self.store, context.root, developer_mode=context.developer_mode, context=context)
        self.title = "PUB-TOOL · " + ("DEBUG · " if context.developer_mode else "") + "Literature Workspace"
        self.controls = None
        for action in APP_ACTIONS:
            router.register_app(action, lambda payload, name=action: self.service.call(name, payload))
        router.register_native("app.open_pubmed", lambda p, native: native.open_url(pubmed_url(p.get("query"))))
        router.register_native("app.developer.styles", self.developer_styles)
        router.register_native("app.developer.reload", self.developer_reload)
        router.register_native("app.developer.notice", self.developer_notice)

    def developer_styles(self, payload, native):
        if not self.context.developer_mode:
            raise ValueError("僅供開發者模式")
        return {"hashes": css_hashes(self.context.root, payload.get("paths"))}

    def developer_reload(self, payload, native):
        if not self.context.developer_mode:
            raise ValueError("僅供開發者模式")
        native.reload()

    def developer_notice(self, payload, native):
        if not self.context.developer_mode:
            raise ValueError("僅供開發者模式")
        native.notice(str(payload.get("message", ""))[:2000])

    def prepare_request(self, action, payload, native):
        action = action.removeprefix("app.")
        if action == 'preview_project_import':
            # File dialogs must run on the GUI thread, before dispatch to the worker.
            from PySide6.QtWidgets import QApplication, QFileDialog
            filename, _ = QFileDialog.getOpenFileName(QApplication.activeWindow(), '匯入研究專案', '',
                'PUB-TOOL 專案 (*.pubtool);;專案 JSON (*.json);;SQLite 專案 (*.sqlite3 *.db)')
            return PreparedRequest({'source': filename}, cancelled=not bool(filename))
        if action == 'export_project':
            name = 'PUB-TOOL_project_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.pubtool'
            filename = native.choose_save_file('匯出研究專案', name, 'PUB-TOOL 專案 (*.pubtool)')
            if not filename:
                return PreparedRequest({}, cancelled=True)
            if not filename.lower().endswith('.pubtool'):
                filename += '.pubtool'
            return PreparedRequest({**payload, 'target': filename})
        if action not in {"export_excel", "export_endnote"}:
            return PreparedRequest(payload)
        ris = action == "export_endnote"
        suffix = ".ris" if ris else ".xlsx"
        name = "PUB-TOOL_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + suffix
        filename = native.choose_save_file("匯出 EndNote RIS" if ris else "匯出文獻與摘要", name,
                                           "EndNote RIS (*.ris)" if ris else "Excel (*.xlsx)")
        if not filename:
            return PreparedRequest({}, cancelled=True)
        if not filename.lower().endswith(suffix):
            filename += suffix
        return PreparedRequest({**payload, "target": filename})

    def attach(self, window):
        if not self.context.developer_mode:
            return
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDockWidget
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from .developer_ui import DeveloperControls
        self.devtools = QWebEngineView(window)
        self.dock = QDockWidget("介面檢查工具 · 來源 CSS 可 PUSH／DOM 與 Console 臨時預覽", window)
        self.dock.setObjectName("pub-tool-devtools")
        self.dock.setWidget(self.devtools)
        window.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.dock)
        window.page.setDevToolsPage(self.devtools.page())
        self.dock.hide()
        action = self.dock.toggleViewAction()
        action.setText("介面檢查工具（F12）")
        action.setShortcut("F12")
        self.controls = DeveloperControls(window, self.service, self.context.hot_reload, action)

    def start_frontend(self, window):
        if self.context.hot_reload and self.controls:
            self.controls.start_server()
            return True
        return False

    def cancel_close(self):
        if self.controls:
            self.controls.close_allowed = False

    def before_close(self):
        if not self.controls:
            return CloseDecision(True)
        if not self.controls.close_allowed:
            self.controls.request_close()
            return CloseDecision(False)
        return CloseDecision(True, 75 if self.controls.restart_requested else 0)

    def shutdown(self):
        if self.controls:
            self.controls.stop_server()

    def health_check(self):
        with self.store._connect() as db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            db.execute("SELECT COUNT(*) FROM projects").fetchone()
        if schema != 1:
            raise ValueError("研究資料 schema 尚未完成初始化")
        return {"data_schema_version": schema}


def create_package(context, router):
    return LiteraturePackage(context, router)
