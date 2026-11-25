from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import httpx

from ..api.providers import HttpxMeshProvider, HttpxPubChemProvider
from ..resolver.cli import _jsonable
from ..resolver.service import TermResolver
from ..workspace.graph import compile_graph, compile_graph_with_status, validate_graph
from ..workspace.store import WorkspaceStore
from ..workspace.literature import LiteratureStore
from ..api.pubmed import PubMedClient
from ..workspace.excel_export import export_xlsx
from ..workspace.endnote_export import export_ris
from ..workspace.project_transfer import read_project_file, preview_bundle, import_project, export_project
from .rendering import rendering_preferences, set_rendering_preferences
from .style_settings import style_settings, save_style_settings, validate_style_values
from .development import DeveloperRepository
from .model_settings import local_model_settings, save_local_model_settings
from ..api.local_model import LocalModelClient, validate_model_settings, model_http_client
from pubtool_core import CORE_VERSION, CORE_API_VERSION
from pubtool_core.operations import update_preferences, runtime_info


class DesktopService:
    """Explicit RPC allowlist shared by the desktop bridge and offline tests."""

    def __init__(self, store: WorkspaceStore, root: Path, developer_mode: bool = False, context=None):
        self.store, self.root = store, root
        self.developer_mode = developer_mode
        self.context = context
        self.development = DeveloperRepository(root) if developer_mode else None
        self.literature = LiteratureStore(store)
        self.project_imports = {}

    def call(self, action: str, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("請求內容必須為物件")
        if action in {'load', 'save', 'history', 'history_preferences', 'set_history_preferences', 'delete_revision',
                      'pubmed_search', 'pubmed_runs', 'pubmed_articles', 'pubmed_fetch', 'screen_article',
                      'reading_html', 'export_excel', 'export_endnote', 'export_project', 'screening_history', 'undo_screening'}:
            self.store.require_active_project(int(payload['project_id']))
        if action == "status":
            manifest = self.root / "release-manifest.json"
            release = json.loads(manifest.read_text("utf-8")) if manifest.exists() else {}
            with self.store._connect() as db:
                schema_version = db.execute("PRAGMA user_version").fetchone()[0]
            versions = self.context.version_fields() if self.context else {
                "version": release.get("version", "0.3.2-dev"), "package_version": release.get("version", "0.3.2-dev"),
                "core_version": CORE_VERSION, "core_api_version": CORE_API_VERSION}
            return {**versions, "data_schema_version": schema_version,
                    "data_dir": str(self.store.path.parent), "database": str(self.store.path),
                    "update_repository": release.get("update_repository"),
                    "update_preferences_available": bool(os.getenv("PYAPPIFY_APP_JSON_PATH")),
                    "developer_mode": self.developer_mode,
                    "source_root": str(self.root) if self.developer_mode else None,
                    "runtime": runtime_info(),
                    "core_dir": str(self.context.core_dir) if self.context else None,
                    "style_settings": style_settings(self.store),
                    "capabilities": {"resolver": True, "query_canvas": True, "pubmed_fetch": True},
                    "projects": self.store.list_projects()}
        if action == "projects":
            return self.store.list_projects()
        if action == 'trashed_projects':
            return self.store.list_trashed_projects()
        if action == 'trash_project':
            return self.store.trash_project(int(payload['project_id']))
        if action == 'restore_project':
            return self.store.restore_project(int(payload['project_id']))
        if action == 'preview_project_import':
            # The picker runs on the GUI thread; parsing runs in the worker.
            bundle = read_project_file(payload['source'])
            token = uuid.uuid4().hex
            cutoff = time.monotonic() - 1800
            self.project_imports = {key: item for key, item in self.project_imports.items() if item[0] > cutoff}
            while len(self.project_imports) >= 4:
                self.project_imports.pop(next(iter(self.project_imports)))
            self.project_imports[token] = (time.monotonic(), bundle)
            return {'token': token, 'filename': Path(payload['source']).name, **preview_bundle(bundle)}
        if action == 'import_project':
            token = payload.get('token')
            if not isinstance(token, str) or token not in self.project_imports:
                raise ValueError('匯入預覽已失效，請重新選擇檔案')
            created, bundle = self.project_imports[token]
            if time.monotonic() - created > 1800:
                self.project_imports.pop(token)
                raise ValueError('匯入預覽已過期，請重新選擇檔案')
            result = import_project(self.store, bundle, payload.get('name'))
            self.project_imports.pop(token)
            return result
        if action == 'export_project':
            return export_project(self.store, int(payload['project_id']), payload['target'])
        if action == "rendering_preferences":
            return rendering_preferences(self.store.path.parent)
        if action == "local_model_settings":
            return local_model_settings(self.store)
        if action == "save_local_model_settings":
            return save_local_model_settings(self.store, payload.get("settings"))
        if action in {"local_model_models", "local_model_test"}:
            settings = (validate_model_settings(payload["settings"]) if action == "local_model_models" and "settings" in payload
                        else local_model_settings(self.store)["settings"])
            with model_http_client(settings) as client:
                model = LocalModelClient(client, settings)
                if action == "local_model_models":
                    return {"models": model.models()}
                return model.complete([{"role": "user", "content": "Reply with PUB-TOOL connection OK."}])
        if action == "style_settings":
            return style_settings(self.store)
        if action == "validate_style_settings":
            return validate_style_values(payload.get("values"))
        if action == "save_style_settings":
            return save_style_settings(self.store, payload.get("values"))
        if action == "set_rendering_preferences":
            return set_rendering_preferences(self.store.path.parent, payload.get("render_mode"))
        if action == "debug_validate":
            if not self.developer_mode:
                raise ValueError("此操作需以 main_debug.py 啟用開發者模式")
            query = payload.get("query", "")
            if not isinstance(query, str) or len(query) > 100_000:
                raise ValueError("搜尋式過長或無效")
            graph = validate_graph(payload.get("graph"))
            try:
                compiled = compile_graph(graph)
                diagnostic = {"generated_query": compiled["query"], "warnings": compiled["warnings"], "compile_error": None}
            except ValueError as exc:
                diagnostic = {"generated_query": None, "warnings": [], "compile_error": str(exc)}
            return {"graph": graph, "query": query, **diagnostic}
        if action in {"debug_git_status", "debug_git_prepare", "debug_git_commit"}:
            if not self.developer_mode or self.development is None:
                raise ValueError("此操作需以 main_debug.py 啟用開發者模式")
            if action == "debug_git_status":
                return self.development.status()
            if action == "debug_git_prepare":
                return self.development.prepare(payload.get("name"), payload.get("edits", []))
            return self.development.commit(payload.get("token"))
        if action == 'export_excel':
            return export_xlsx(self.store, int(payload['project_id']), int(payload['run_id']), Path(payload['target']), payload.get('scope', 'ALL'))
        if action == 'export_endnote':
            return export_ris(self.store, int(payload['project_id']), int(payload['run_id']), Path(payload['target']), payload.get('scope', 'ALL'))
        if action == 'pubmed_search':
            project_id = int(payload['project_id'])
            self.store.load(project_id)
            with httpx.Client(timeout=45) as client:
                result = PubMedClient(client).search(payload['query'], payload.get('limit', 500))
            run_id = self.literature.save_search(project_id, result, app_version=self.context.package_version if self.context else "0.3.2-dev")
            return {'run_id': run_id, 'count': result['count'], 'collected': len(result['pmids']), 'complete': result['complete']}
        if action == 'pubmed_runs':
            return self.literature.runs(int(payload['project_id']))
        if action == 'pubmed_articles':
            return self.literature.articles(int(payload['project_id']), int(payload['run_id']))
        if action == 'pubmed_fetch':
            project_id, run_id = int(payload['project_id']), int(payload['run_id'])
            pmids = self.literature.pending(project_id, run_id, payload.get('retry_failed') is True)
            if not pmids:
                return {'fetched': 0, 'requested': 0, 'error': None}
            records, error = [], None
            try:
                with httpx.Client(timeout=60) as client:
                    records = PubMedClient(client).fetch(pmids)
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) else '無法解析 EFetch 回應，請檢查連線後手動重試'
            self.literature.save_batch(project_id, run_id, pmids, records, error)
            return {'fetched': len(records), 'requested': len(pmids), 'error': error}
        if action == 'screen_article':
            self.literature.screen(int(payload['project_id']), int(payload['run_id']), payload['pmid'], payload['status'], payload.get('notes', ''), payload.get('reason', ''))
            return {'saved': True}
        if action == 'screening_history':
            return self.literature.screening_history(int(payload['project_id']), payload.get('limit', 25))
        if action == 'undo_screening':
            return self.literature.undo_screening(int(payload['project_id']), payload.get('event_id'))
        if action == 'reading_html':
            return self.literature.export_html(int(payload['project_id']), int(payload['run_id']), payload.get('only_kept') is True, payload.get('pmid'))
        if action == "create_project":
            return self.store.create_project(payload.get("name"))
        if action == "load":
            return self.store.load(int(payload["project_id"]), payload.get("revision"))
        if action == "save":
            return self.store.save(int(payload["project_id"]), int(payload["expected_revision"]), payload["graph"], payload.get("query", ""))
        if action == "history":
            return self.store.history(int(payload["project_id"]))
        if action == "history_preferences":
            return self.store.history_preferences(int(payload["project_id"]))
        if action == "set_history_preferences":
            return self.store.set_history_preferences(int(payload["project_id"]), payload.get("auto_prune"), payload.get("keep_latest"))
        if action == "delete_revision":
            return self.store.delete_revision(int(payload["project_id"]), payload.get("revision"), payload.get("expected_revision"))
        if action == "validate_graph":
            return validate_graph(payload["graph"])
        if action == "compile":
            return compile_graph(payload["graph"])
        if action == "compile_with_status":
            return compile_graph_with_status(payload["graph"])
        if action == "resolve":
            term = payload.get("term")
            if not isinstance(term, str) or not term.strip() or len(term) > 300:
                raise ValueError("詞彙需為 1–300 字")
            entity_type = payload.get("entity_type", "compound")
            if entity_type not in {"compound", "unknown"}:
                raise ValueError("不支援的詞彙類型")
            with httpx.Client(timeout=20, follow_redirects=False) as client:
                result = TermResolver(HttpxMeshProvider(client), HttpxPubChemProvider(client)).resolve(term, entity_type=entity_type)
            return _jsonable(result, verbose=True)
        if action == "update_preferences":
            return update_preferences(payload)
        raise ValueError("不支援的操作")


def pubmed_url(query: str) -> str:
    from urllib.parse import urlencode
    if not isinstance(query, str) or not query.strip() or len(query) > 100_000:
        raise ValueError("搜尋式為空或過長")
    return "https://pubmed.ncbi.nlm.nih.gov/?" + urlencode({"term": query})
