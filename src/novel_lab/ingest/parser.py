"""TXT/EPUB → 章节列表 解析器。

中文章节正则覆盖：
    第\\d+章 / 第一章 / 第十二回 / 第123节 / 卷一 第三章 ... 等
也支持纯空行分段的"标题行"启发式（标题行短 + 不以标点结尾）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterator

import regex
from ebooklib import epub
from bs4 import BeautifulSoup

from ..schema import Chapter, NovelMeta


# 中文数字 + 阿拉伯数字 通用章节正则
_CN_NUM = r"[零〇一二三四五六七八九十百千万两\d]+"
# 章节：章 / 回 / 节 / 折（不含 卷）
_CHAPTER_RE = regex.compile(
    rf"^[\s　]*(?:第\s*{_CN_NUM}\s*[章回节折])"
    rf"(?:[\s　]+|[:：、\.\-—_]+)?"
    rf"(.{{0,80}}?)\s*$",
    flags=regex.MULTILINE,
)
_VOLUME_RE = regex.compile(
    rf"^[\s　]*(?:第\s*{_CN_NUM}\s*卷"
    rf"(?:[\s　]+|[:：])?(.{{0,80}}?))\s*$",
    flags=regex.MULTILINE,
)


def _book_id(text: str, title: str) -> str:
    h = hashlib.sha1((title + text[:5000]).encode("utf-8", errors="ignore")).hexdigest()
    return f"book_{h[:12]}"


# ---------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------


def _read_text_robust(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _iter_chapter_spans(text: str) -> Iterator[tuple[int, int, str]]:
    """yield (start_pos, end_pos, title)."""
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        # 没有章节标识，整本作为一章
        yield 0, len(text), "全文"
        return
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = m.group(0).strip()
        yield start, end, title


def _detect_volume(title_line: str) -> str | None:
    m = _VOLUME_RE.search(title_line)
    return m.group(0).strip() if m else None


def parse_txt(path: Path, *, title: str | None = None) -> NovelMeta:
    text = _read_text_robust(path).replace("\r\n", "\n").replace("\r", "\n")
    title = title or path.stem

    chapters: list[Chapter] = []
    current_volume: str | None = None
    current_volume_idx = -1

    for idx, (start, end, raw_title) in enumerate(_iter_chapter_spans(text)):
        body = text[start:end].lstrip("\r\n\u3000 \t")
        body_lines = body.splitlines()
        chapter_title = body_lines[0].strip() if body_lines else f"第{idx+1}章"
        chapter_text = "\n".join(body_lines[1:]).strip()

        # 检查上方是否有卷标
        backwards = text[max(0, start - 200):start]
        vol = _detect_volume(backwards)
        if vol and vol != current_volume:
            current_volume = vol
            current_volume_idx += 1

        chapters.append(
            Chapter(
                idx=idx,
                title=chapter_title,
                text=chapter_text,
                char_count=len(chapter_text),
                volume=current_volume,
                volume_idx=current_volume_idx if current_volume else None,
            )
        )

    total_chars = sum(c.char_count for c in chapters)
    return NovelMeta(
        book_id=_book_id(text, title),
        title=title,
        total_chapters=len(chapters),
        total_chars=total_chars,
        chapters=chapters,
    )


# ---------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------


def parse_epub(path: Path) -> NovelMeta:
    book = epub.read_epub(str(path))
    title = book.get_metadata("DC", "title")
    title_str = title[0][0] if title else path.stem
    author_meta = book.get_metadata("DC", "creator")
    author = author_meta[0][0] if author_meta else None

    # 把所有 HTML 拼起来再走 TXT 切分逻辑（容错好）
    parts: list[str] = []
    from ebooklib import ITEM_DOCUMENT  # local import to avoid hard dep
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "lxml")
        for br in soup.find_all("br"):
            br.replace_with("\n")
        text = soup.get_text("\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        parts.append(text.strip())

    full_text = "\n\n".join(parts)
    # 写到临时位置走 parse_txt 的核心逻辑
    meta = NovelMeta(
        book_id=_book_id(full_text, title_str),
        title=title_str,
        author=author,
        total_chapters=0,
        total_chars=0,
    )
    chapters: list[Chapter] = []
    for idx, (start, end, raw_title) in enumerate(_iter_chapter_spans(full_text)):
        body = full_text[start:end].lstrip("\r\n\u3000 \t").splitlines()
        chapter_title = body[0].strip() if body else f"第{idx+1}章"
        chapter_text = "\n".join(body[1:]).strip()
        chapters.append(
            Chapter(
                idx=idx,
                title=chapter_title,
                text=chapter_text,
                char_count=len(chapter_text),
            )
        )
    meta.chapters = chapters
    meta.total_chapters = len(chapters)
    meta.total_chars = sum(c.char_count for c in chapters)
    return meta


def parse(path: str | Path, *, title: str | None = None) -> NovelMeta:
    p = Path(path)
    if p.suffix.lower() in {".epub"}:
        return parse_epub(p)
    return parse_txt(p, title=title)
