from __future__ import annotations
import sqlite3
from pathlib import Path

def initialize_database(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    schema_path = Path(__file__).resolve().parents[3] / "database" / "schema.sql"
    connection.executescript(schema_path.read_text(encoding="utf-8"))
    connection.commit()
    return connection
