"""Developer-only native toolbar and an APP-owned loopback Vite process."""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import uuid
from datetime import datetime, timezone

from PySide6.QtCore import QFileSystemWatcher, QObject, QProcess, QTimer, QUrl
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                              QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                              QToolButton, QVBoxLayout, QWidget)


class DeveloperControls(QObject):
    def __init__(self, window, service, hot_reload, devtools_action):
        super().__init__(window)
        self.window, self.service, self.root = window, service, service.root
        self.bridge, self.page = window.bridge, window.page
        self.hot_reload = hot_reload
        self.pending = {}
        self.reload_pending = False
        self.reloading = False
        self.backend_changed = False
        self.process = None
        self.source_origin = None
        self.stdout = self.stderr = None
        self.output_buffer = b""
        self.started = False
        self.preview_open = False
        self.saving = False
        self.close_check_pending = False
        self.close_allowed = False
        self.restart_requested = False
        self.after_reload_message = None
        widget = QWidget(window)
        row = QHBoxLayout(widget)
        row.setContentsMargins(8, 4, 8, 4)
        menu_button = QToolButton(widget)
        menu_button.setText("開發者 Debug")
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(menu_button)
        menu.addAction(devtools_action)
        menu_button.setMenu(menu)
        row.addWidget(menu_button)
        row.addWidget(QLabel("版本名稱", widget))
        self.version = QLineEdit(widget)
        self.version.setMaxLength(80)
        self.version.setMaximumWidth(280)
        row.addWidget(self.version)
        self.revision = QLabel("讀取 Git…", widget)
        row.addWidget(self.revision)
        self.push = QPushButton("PUSH 儲存", widget)
        self.push.setToolTip("預覽來源差異，確認後建立本地 Git commit；不發布 UPDATE")
        self.push.clicked.connect(self.prepare)
        self.push.setEnabled(False)
        row.addWidget(self.push)
        self.include_css = QCheckBox("含 F12 來源 CSS", widget)
        self.include_css.setChecked(hot_reload)
        self.include_css.setEnabled(hot_reload)
        self.include_css.setToolTip("只回寫自有 stylesheet 規則；DOM、element.style、Console 不回寫")
        row.addWidget(self.include_css)
        refresh = QPushButton("重新載入", widget)
        refresh.clicked.connect(lambda: self.request_reload(explicit=True))
        row.addWidget(refresh)
        self.restart = QPushButton("套用 Python 修改", widget)
        self.restart.setEnabled(False)
        self.restart.clicked.connect(self.restart_backend)
        row.addWidget(self.restart)
        row.addStretch()
        window.setMenuWidget(widget)
        self.bridge.response.connect(self.response)
        self.bridge.reloadRequested.connect(self.request_reload)
        self.bridge.developerMessage.connect(self.message)
        self.bridge.dirtyChanged.connect(lambda _: self.resume_reload())
        self.page.loadFinished.connect(self.loaded)
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.setInterval(200)
        self.reload_timer.timeout.connect(self.try_reload)
        self.start_timer = QTimer(self)
        self.start_timer.setSingleShot(True)
        self.start_timer.setInterval(20000)
        self.start_timer.timeout.connect(self.server_timeout)
        self.watch_timer = QTimer(self)
        self.watch_timer.setSingleShot(True)
        self.watch_timer.setInterval(250)
        self.watch_timer.timeout.connect(self.backend_event)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _: self.watch_timer.start())
        self.watcher.fileChanged.connect(lambda _: self.watch_timer.start())
        self.backend_state = self.backend_sources()
        self.watch_backend()
        self.call("debug_git_status", {}, self.show_status)

    def message(self, text):
        self.window.statusBar().showMessage(str(text))

    def call(self, action, payload, callback):
        ident = "native-dev:" + uuid.uuid4().hex
        self.pending[ident] = callback
        self.bridge.request(json.dumps({"id": ident, "action": action, "payload": payload}, ensure_ascii=False))

    def response(self, raw):
        response = json.loads(raw)
        callback = self.pending.pop(response.get("id"), None)
        if callback:
            if response.get("ok"):
                callback(response["result"])
            else:
                self.set_saving(False)
                self.message(response.get("error", "操作失敗"))
                QMessageBox.warning(self.window, "開發者操作未完成", response.get("error", "操作失敗"))
        self.resume_reload()

    def show_status(self, status):
        self.version.setText(status["name"])
        self.revision.setText(f'{status["branch"]} · {status["head"][:8]}')
        self.revision.setToolTip(status.get("changes") or "來源沒有未提交變更")
        self.set_saving(False)

    def set_saving(self, saving):
        self.saving = saving
        self.push.setEnabled(not saving)
        self.version.setEnabled(not saving)
        self.include_css.setEnabled(self.hot_reload and not saving)

    def prepare(self):
        if self.bridge.jobs or self.preview_open:
            self.message("請等待目前操作完成後再 PUSH")
            return
        self.set_saving(True)
        if self.include_css.isChecked():
            self.page.runJavaScript("JSON.stringify(window.pubtoolDeveloper?.changes() || {error:'熱更新頁面尚未就緒'})", self.captured)
        else:
            self.captured('{"edits":[]}')

    def captured(self, raw):
        try:
            result = json.loads(raw or "{}")
            if result.get("error") or not isinstance(result.get("edits"), list):
                raise ValueError(result.get("error") or "無法讀取來源 CSS")
            self.call("debug_git_prepare", {"name": self.version.text(), "edits": result["edits"]}, self.review)
        except (TypeError, ValueError) as exc:
            self.set_saving(False)
            self.message(str(exc))

    def review(self, plan):
        self.preview_open = True
        try:
            dialog = QDialog(self.window)
            dialog.setWindowTitle("PUSH 儲存 · 本地 Git 差異確認")
            dialog.resize(960, 680)
            layout = QVBoxLayout(dialog)
            view = QPlainTextEdit(dialog)
            view.setReadOnly(True)
            view.setPlainText(plan["preview"])
            layout.addWidget(view)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("確認儲存本地 Git")
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.call("debug_git_commit", {"token": plan["token"]}, self.saved)
            else:
                self.set_saving(False)
        finally:
            self.preview_open = False
            self.resume_reload()

    def saved(self, result):
        self.version.setText(result["name"])
        summary = result["message"] + " · " + result["head"][:8]
        self.message(summary)

        def refreshed(status):
            self.show_status(status)
            if result.get("saved") and self.hot_reload:
                # Reuse the same save/CSS guards as source-triggered reloads.
                # Never reload while the snapshot or status job is in flight.
                self.after_reload_message = summary + "；已重新載入儲存後的來源"
                self.request_reload()
            elif result.get("saved"):
                self.message(summary + "；目前為靜態模式，請用 main_debug.py 重新建置並套用來源")
            else:
                self.message(summary + "；DOM／inline 修改不會回寫來源")

        self.call("debug_git_status", {}, refreshed)

    def loaded(self, ok):
        self.reloading = False
        if ok:
            self.page.developer_loaded = True
            self.message(self.after_reload_message or ("DEBUG · CSS 即時更新；TS/TSX 儲存草稿後重載；PUSH 只存本地 Git" if self.hot_reload else "DEBUG · 靜態 bundle（使用 main_debug.py 啟用熱更新）"))
            self.after_reload_message = None

    def request_reload(self, explicit=False):
        self.reload_pending = True
        self.explicit_reload = bool(explicit)
        self.reload_timer.start()

    def resume_reload(self):
        if self.reload_pending and not self.reloading:
            self.reload_timer.start()

    def try_reload(self):
        if not self.reload_pending or self.reloading:
            return
        if self.bridge.dirty or self.bridge.jobs or self.preview_open or self.close_check_pending or self.saving:
            prefix = "Git 已儲存，畫面更新待套用" if self.after_reload_message else "來源已修改"
            self.message(prefix + "：請先儲存／捨棄畫布、閱讀備註、設定與 Debug 草稿，再重載")
            return
        self.page.runJavaScript("JSON.stringify(window.pubtoolDeveloper?.changes() || {edits:[]})", self.reload_checked)

    def reload_checked(self, raw):
        if not self.reload_pending or self.reloading or self.bridge.dirty or self.bridge.jobs or self.preview_open or self.saving:
            return
        try:
            css = json.loads(raw or "{}")
        except (ValueError, TypeError):
            self.message("無法確認 F12 草稿，請稍後手動重新載入")
            return
        if css.get("error"):
            self.message(css["error"])
            return
        if css.get("edits"):
            if not getattr(self, "explicit_reload", False):
                self.message("F12 CSS 尚未 PUSH：請先儲存，或按「重新載入」明確捨棄")
                return
            if QMessageBox.question(self.window, "捨棄 F12 CSS？", "尚有未回寫的 F12 來源 CSS。確定捨棄並重載？") != QMessageBox.StandardButton.Yes:
                return
        self.reload_pending = False
        self.reloading = True
        self.page.allow_developer_reload = True
        self.page.triggerAction(self.page.WebAction.ReloadAndBypassCache)

    def backend_sources(self):
        files = [self.root / "main.py", *self.root.glob("backend/literature_app/**/*.py")]
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}

    def watch_backend(self):
        directories = [self.root / "backend/literature_app", *self.root.glob("backend/literature_app/**/")]
        candidates = {str(p) for p in directories if p.is_dir() and "__pycache__" not in p.parts}
        candidates.update(p for p in self.backend_state if (self.root / p).is_file())
        existing = set(self.watcher.files() + self.watcher.directories())
        missing = list(candidates - existing)
        if missing:
            self.watcher.addPaths(missing)

    def backend_event(self):
        try:
            current = self.backend_sources()
        except OSError as exc:
            self.message("Python 來源暫不可讀，請完成檔案儲存後再套用：" + str(exc))
            return
        if current != self.backend_state:
            self.backend_changed = True
            self.restart.setEnabled(self.hot_reload)
            self.message("Python 來源已修改：儲存研究草稿後按「套用 Python 修改」完整重啟")
        self.backend_state = current
        self.watch_backend()

    def restart_backend(self):
        if self.bridge.jobs or self.bridge.dirty or self.preview_open or self.saving:
            self.message("請先處理未儲存草稿並等待操作完成，再套用 Python 修改")
            return
        self.request_close(restart=True)

    def request_close(self, restart=False):
        if self.close_check_pending or self.preview_open or self.saving:
            return
        self.close_check_pending = True
        if self.hot_reload:
            self.page.runJavaScript("JSON.stringify(window.pubtoolDeveloper?.changes() || {edits:[]})", lambda raw: self.close_checked(raw, restart))
        else:
            self.close_checked('{"edits":[]}', restart)

    def close_checked(self, raw, restart):
        self.close_check_pending = False
        if self.bridge.dirty or self.bridge.jobs or self.preview_open:
            self.message("請先儲存研究草稿並等待操作完成")
            return
        try:
            changes = json.loads(raw or "{}")
        except (ValueError, TypeError):
            changes = {"error": "無法確認 F12 草稿"}
        prompt = ""
        if changes.get("edits") or changes.get("error"):
            prompt = "尚有未回寫的 F12 CSS 或無法確認樣式狀態。關閉會捨棄它們，可先取消並 PUSH。\n"
        if restart:
            prompt += "將完整重啟 Python Debug APP；磁碟上的未提交來源檔仍保留。\n"
        if prompt and QMessageBox.question(self.window, "確認重啟" if restart else "確認關閉", prompt + "繼續？") != QMessageBox.StandardButton.Yes:
            return
        self.restart_requested = restart
        self.close_allowed = True
        self.window.close()

    def start_server(self):
        node = shutil.which("node.exe") or shutil.which("node")
        if not node or not (self.root / "frontend/node_modules/vite/bin/vite.js").is_file() or not (self.root / "dev-version.json").is_file():
            raise ValueError("熱更新需要開發來源、Node.js 與 frontend/node_modules；請先執行 main_debug.py")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.source_origin = QUrl(f"http://127.0.0.1:{port}")
        self.window.interceptor.dev_origin = self.source_origin
        self.page.dev_origin = self.source_origin
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ")
        prefix = self.root / "logs/debug" / ("vite_" + stamp)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        self.process_report = prefix.with_suffix(".process.json")
        self.stdout = prefix.with_suffix(".stdout.log").open("xb")
        self.stderr = prefix.with_suffix(".stderr.log").open("xb")
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(self.root / "frontend"))
        self.process.setProgram(node)
        self.process.setArguments([str(self.root / "frontend/dev-server.mjs"), str(port)])
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.started.connect(self.server_started)
        self.process.finished.connect(self.server_finished)
        self.process.errorOccurred.connect(self.server_error)
        self.message("啟動 APP 管理的 loopback Vite；日誌 " + str(prefix))
        self.start_timer.start()
        self.process.start()

    def server_started(self):
        self.started = True

    def read_stdout(self):
        data = bytes(self.process.readAllStandardOutput())
        if self.stdout and not self.stdout.closed:
            self.stdout.write(data)
            self.stdout.flush()
        self.output_buffer += data
        while b"\n" in self.output_buffer:
            line, self.output_buffer = self.output_buffer.split(b"\n", 1)
            if line.startswith(b"PUBTOOL_DEV_READY "):
                self.start_timer.stop()
                ready = json.loads(line.split(b" ", 1)[1])
                if ready.get("port") == self.source_origin.port():
                    self.window.view.load(QUrl(self.source_origin.toString() + "/debug.html"))

    def read_stderr(self):
        data = bytes(self.process.readAllStandardError())
        if self.stderr and not self.stderr.closed:
            self.stderr.write(data)
            self.stderr.flush()

    def server_error(self, error):
        self.start_timer.stop()
        self.message("Vite 啟動／執行錯誤：" + self.process.errorString())
        if error == QProcess.ProcessError.FailedToStart:
            self.write_process_report(None, False)

    def server_timeout(self):
        self.message("Vite 未在 20 秒內就緒，請檢查 logs/debug/vite_*；不會自動重試")
        self.stop_server()

    def write_process_report(self, code, completed):
        self.process_report.write_text(json.dumps({"completed": completed, "exit_code": code,
            "started": self.started, "finished_utc": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
        for output in (self.stdout, self.stderr):
            if output and not output.closed:
                output.close()

    def server_finished(self, code, status):
        self.start_timer.stop()
        self.read_stdout()
        self.read_stderr()
        self.write_process_report(code, True)
        self.message(f"Vite 已結束（exit {code}）；若非關閉 APP，請保留 logs/debug/vite_* 日誌")

    def stop_server(self):
        self.start_timer.stop()
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill()
                self.process.waitForFinished(3000)
