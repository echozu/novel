"""SQLite 缓存：以 (model, messages, kwargs) 哈希作为 key，命中即跳过 API 调用。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


class LLMCache:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS llm_cache (
                    key TEXT PRIMARY KEY,
                    model TEXT,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_model ON llm_cache(model)")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def make_key(model: str, payload: dict[str, Any]) -> str:
        norm = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{model}|{norm}".encode()).hexdigest()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT payload FROM llm_cache WHERE key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def set(self, key: str, model: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO llm_cache(key, model, payload, created_at) VALUES (?,?,?,?)",
                (key, model, payload, time.time()),
            )
            c.commit()
