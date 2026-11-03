from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from ..api.providers import HttpxMeshProvider, HttpxPubChemProvider
from ..resolver.cli import _jsonable
from ..resolver.service import TermResolver
from ..workspace.graph import compile_graph, validate_graph
from ..workspace.store import WorkspaceStore
from ..workspace.literature import LiteratureStore
from ..api.pubmed import PubMedClient
from ..workspace.excel_export import export_xlsx


class DesktopService:
    """Explicit RPC allowlist shared by the desktop bridge and offline tests."""

    def __init__(self, store: WorkspaceStore, root: Path):
        self.store, self.root = store, root
        self.literature = LiteratureStore(store)

    def call(self, action: str, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("請求內容必須為物件")
        if action == "status":
            manifest = self.root / "release-manifest.json"
            release = json.loads(manifest.read_text("utf-8")) if manifest.exists() else {}
            return {"version": release.get("version", "0.3.0-dev"),
                    "data_dir": str(self.store.path.parent), "database": str(self.store.path),
                    "update_repository": release.get("update_repository"),
                    "update_preferences_available": bool(os.getenv("PYAPPIFY_APP_JSON_PATH")),
                    "capabilities": {"resolver": True, "query_canvas": True, "pubmed_fetch": True},
                    "projects": self.store.list_projects()}
        if action == "projects":
            return self.store.list_projects()
        if action == 'export_excel':
            return export_xlsx(self.store, int(payload['project_id']), int(payload['run_id']), Path(payload['target']), payload.get('scope', 'ALL'))
        if action == 'pubmed_search':
            project_id = int(payload['project_id'])
            self.store.load(project_id)
            with httpx.Client(timeout=45) as client:
                result = PubMedClient(client).search(payload['query'], payload.get('limit', 500))
            run_id = self.literature.save_search(project_id, result)
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
        if action == "validate_graph":
            return validate_graph(payload["graph"])
        if action == "compile":
            return compile_graph(payload["graph"])
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
        raise ValueError("不支援的操作")


def pubmed_url(query: str) -> str:
    from urllib.parse import urlencode
    if not isinstance(query, str) or not query.strip() or len(query) > 100_000:
        raise ValueError("搜尋式為空或過長")
    return "https://pubmed.ncbi.nlm.nih.gov/?" + urlencode({"term": query})
