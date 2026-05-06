"""评论爬虫骨架 — 番茄/起点。

策略：
- 起点（章评较丰富）：网页端章末评论 / 段评接口（PC 端 page-comment）
- 番茄：移动端接口需要 token，PC 端可解析；建议走 Playwright 兜底
- 强烈建议先 ``GET /robots.txt`` 检查并尊重，限速 1s/req，UA 轮换
- 仅供学习用途；如需大量数据请联系平台获取 API
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Iterable, Optional

from .schema import RawComment


_DEFAULT_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0 Safari/537.36",
]


@dataclass
class CrawlerConfig:
    source: str          # qidian | fanqie
    book_id: str
    out_dir: Path
    rate_limit_seconds: float = 1.5
    max_pages_per_chapter: int = 3
    chapter_idx_range: Optional[tuple[int, int]] = None
    user_agents: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.user_agents:
            self.user_agents = list(_DEFAULT_UAS)


class BaseCrawler:
    """异步爬虫基类（用 httpx，不强依赖 Scrapy/Playwright）。"""

    def __init__(self, config: CrawlerConfig) -> None:
        self.config = config

    async def crawl(self) -> AsyncIterator[RawComment]:  # pragma: no cover
        raise NotImplementedError

    async def crawl_to_jsonl(self, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with dest.open("w", encoding="utf-8") as f:
            async for c in self.crawl():
                f.write(c.model_dump_json() + "\n")
                n += 1
        return n

    def _rand_ua(self) -> str:
        return random.choice(self.config.user_agents)

    async def _wait(self) -> None:
        await asyncio.sleep(
            self.config.rate_limit_seconds + random.uniform(0, self.config.rate_limit_seconds / 2)
        )


class QidianChapterCommentCrawler(BaseCrawler):
    """起点章末评论爬虫（骨架）。

    实际接口路径请参考起点 PC 端 `book.qidian.com/{book_id}` 章节页里的
    AJAX 调用（path 形如 ``/ajax/Comment/Index?bookId=...&chapterId=...``）。
    """

    BASE = "https://book.qidian.com"

    async def crawl(self) -> AsyncIterator[RawComment]:  # pragma: no cover
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError(
                "需要 httpx；请安装 phase2 extras：pip install -e \".[phase2]\""
            ) from exc

        async with httpx.AsyncClient(headers={"User-Agent": self._rand_ua()},
                                     timeout=20.0, follow_redirects=True) as client:
            chap_low, chap_high = self.config.chapter_idx_range or (0, 10**9)
            # TODO: 第一步：拉取目录得到 chapterId 列表（需登录态/cookie）
            # TODO: 第二步：迭代每个 chapterId 拉取评论
            yield  # type: ignore[misc]


class FanqieCommentCrawler(BaseCrawler):
    """番茄章评爬虫（骨架）。

    番茄移动端接口需要签名鉴权；推荐走 Playwright 模拟用户访问 PC 端
    阅读页 `fanqienovel.com/page/{book_id}` 的章评弹层。
    """

    async def crawl(self) -> AsyncIterator[RawComment]:  # pragma: no cover
        # TODO: 用 Playwright 启动 headless 浏览器，注入 cookie 后翻页采集
        yield  # type: ignore[misc]


def load_jsonl(path: Path) -> Iterable[RawComment]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield RawComment(**json.loads(line))


__all__ = [
    "BaseCrawler",
    "CrawlerConfig",
    "QidianChapterCommentCrawler",
    "FanqieCommentCrawler",
    "load_jsonl",
]
