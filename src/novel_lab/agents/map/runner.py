"""Map 阶段并行调度 — 并发控制 + checkpoint。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Optional

from ...llm import LLMRouter
from ...schema import Chapter, ChapterAnalysis
from .chapter_agent import ChapterMapAgent


class MapCheckpoint:
    """SQLite 持久化每章 Map 结果，断点续跑。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS map_results ("
                "  chapter_idx INTEGER PRIMARY KEY,"
                "  payload TEXT NOT NULL"
                ")"
            )

    def _conn(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, idx: int) -> Optional[ChapterAnalysis]:
        with self._lock:
            with self._conn() as c:
                row = c.execute(
                    "SELECT payload FROM map_results WHERE chapter_idx=?", (idx,)
                ).fetchone()
        if row is None:
            return None
        return ChapterAnalysis(**json.loads(row[0]))

    def set(self, item: ChapterAnalysis) -> None:
        payload = item.model_dump_json()
        with self._lock:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO map_results(chapter_idx, payload) VALUES (?,?)",
                    (item.chapter_idx, payload),
                )
                c.commit()

    def all(self) -> list[ChapterAnalysis]:
        with self._lock:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT payload FROM map_results ORDER BY chapter_idx"
                ).fetchall()
        return [ChapterAnalysis(**json.loads(r[0])) for r in rows]


async def run_map(
    chapters: list[Chapter],
    *,
    router: LLMRouter,
    genre: str,
    workdir: Path,
    book_id: str,
    concurrency: int = 20,
    progress_cb: Optional[Callable[[int, int, ChapterAnalysis], None]] = None,
    resume: bool = True,
) -> list[ChapterAnalysis]:
    ckpt = MapCheckpoint(workdir / book_id / "checkpoints" / "map.sqlite")
    agent = ChapterMapAgent(router=router, genre=genre)
    sem = asyncio.Semaphore(concurrency)
    chapter_timeout = float(os.getenv("MAP_CHAPTER_TIMEOUT_SEC", "420"))

    async def worker(ch: Chapter) -> ChapterAnalysis:
        if resume:
            cached = ckpt.get(ch.idx)
            if cached and cached.summary and not cached.summary.startswith("<"):
                return cached
        async with sem:
            try:
                result = await asyncio.wait_for(
                    agent.analyze(ch), timeout=chapter_timeout
                )
            except asyncio.TimeoutError:
                result = ChapterAnalysis(
                    chapter_idx=ch.idx,
                    summary=f"<map 超时：单章分析超过 {chapter_timeout:.0f}s>",
                )
            except Exception as exc:
                result = ChapterAnalysis(
                    chapter_idx=ch.idx,
                    summary=f"<map 失败：{type(exc).__name__}: {exc}>",
                )
        ckpt.set(result)
        return result

    tasks = [asyncio.create_task(worker(ch)) for ch in chapters]
    done: list[ChapterAnalysis] = [None] * len(chapters)  # type: ignore[list-item]
    completed = 0
    for fut in asyncio.as_completed(tasks):
        item = await fut
        # 找回它在原 list 中的位置（顺序无关，按 chapter_idx 索引）
        pos = next(i for i, ch in enumerate(chapters) if ch.idx == item.chapter_idx)
        done[pos] = item
        completed += 1
        if progress_cb:
            try:
                loop = asyncio.get_running_loop()

                def _emit() -> None:
                    try:
                        progress_cb(completed, len(chapters), item)
                    except Exception:
                        pass

                loop.call_soon(_emit)
            except RuntimeError:
                try:
                    progress_cb(completed, len(chapters), item)
                except Exception:
                    pass
    return [d for d in done if d is not None]
