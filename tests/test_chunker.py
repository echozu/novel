from novel_lab.ingest.chunker import estimate_tokens, split_chapter
from novel_lab.schema import Chapter


def test_split_chapter_basic():
    text = "第一段。" * 200
    ch = Chapter(idx=0, title="第一章", text=text, char_count=len(text))
    chunks = split_chapter(ch, chunk_size=200, chunk_overlap=40)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.chapter_idx == 0
        assert c.char_count > 0
        assert c.token_estimate > 0


def test_split_empty_chapter():
    ch = Chapter(idx=0, title="空", text="", char_count=0)
    assert split_chapter(ch) == []


def test_estimate_tokens_chinese():
    assert estimate_tokens("你好世界") > 0
