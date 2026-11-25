from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import uuid

SCHEMA_VERSION = 1

def initialize_database(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    schema_path = Path(__file__).resolve().parents[3] / "database" / "schema.sql"
    try:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise ValueError(f"資料庫 schema {current} 比功能包支援的 {SCHEMA_VERSION} 新；未執行降版")
        connection.execute("PRAGMA journal_mode = WAL")
        if current == 0:
            # Adopt the existing idempotent schema as migration 1. A consistent
            # backup of legacy research data is taken before any DDL/version write.
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                backups = db_path.parent / "backups"
                backups.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%fZ")
                backup = backups / ("pre_schema_1_" + stamp + "_" + uuid.uuid4().hex[:8] + ".sqlite3")
                with sqlite3.connect(backup) as destination:
                    connection.backup(destination)
            connection.executescript("BEGIN IMMEDIATE;\n" + schema_path.read_text(encoding="utf-8")
                                     + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection
