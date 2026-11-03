from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..database.connection import initialize_database
from .graph import validate_graph


class RevisionConflict(ValueError):
    pass


def application_data_dir() -> Path:
    override = os.getenv("PUB_TOOL_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "PUB-TOOL"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PUB-TOOL"
    return Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "pub-tool"


class WorkspaceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        connection = initialize_database(self.path)
        connection.close()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_projects(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("""
                SELECT p.id, p.name, p.description, p.updated_at,
                    COALESCE(MAX(w.revision), 0) AS revision
                FROM projects p LEFT JOIN workspace_revisions w ON w.project_id=p.id
                GROUP BY p.id ORDER BY p.updated_at DESC, p.id DESC
            """)]

    def create_project(self, name: str) -> dict:
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("專案名稱需為 1–120 字")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO projects(name,created_at,updated_at) VALUES(?,?,?)", (name.strip(), now, now))
            ident = cursor.lastrowid
        return self.load(ident)

    def load(self, project_id: int, revision: int | None = None) -> dict:
        with self._connect() as connection:
            project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise ValueError("找不到專案")
            if revision is None:
                row = connection.execute("SELECT * FROM workspace_revisions WHERE project_id=? ORDER BY revision DESC LIMIT 1", (project_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM workspace_revisions WHERE project_id=? AND revision=?", (project_id, revision)).fetchone()
                if row is None:
                    raise ValueError("找不到版本")
        return {"project": dict(project), "revision": row["revision"] if row else 0,
                "graph": json.loads(row["graph_json"]) if row else {"version": 1, "nodes": [], "edges": []},
                "query": row["query_text"] if row else ""}

    def save(self, project_id: int, expected_revision: int, graph: object, query: str) -> dict:
        clean = validate_graph(graph)
        if not isinstance(query, str) or len(query) > 100_000:
            raise ValueError("搜尋式過長或無效")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                raise ValueError("找不到專案")
            current = connection.execute("SELECT COALESCE(MAX(revision),0) FROM workspace_revisions WHERE project_id=?", (project_id,)).fetchone()[0]
            if current != expected_revision:
                raise RevisionConflict("專案已由其他視窗更新。請先匯出草稿，再重新載入。")
            revision = current + 1
            connection.execute("INSERT INTO workspace_revisions VALUES(?,?,?,?,?)", (project_id, revision, json.dumps(clean, ensure_ascii=False, allow_nan=False), query, now))
            connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        # Return the exact committed revision even if another window saves next.
        return self.load(project_id, revision)

    def history(self, project_id: int) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute("SELECT revision,created_at FROM workspace_revisions WHERE project_id=? ORDER BY revision DESC LIMIT 100", (project_id,))]
