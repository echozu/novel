from novel_lab.ingest.parser import parse


def test_parse_sample_chapters(sample_novel_path):
    meta = parse(sample_novel_path)
    assert meta.total_chapters >= 7, "示例小说至少 7 章"
    assert meta.total_chars > 1500
    titles = [c.title for c in meta.chapters]
    assert any("第一章" in t for t in titles)
    assert any("少年下山" in t for t in titles)
    # chapter idx 应该从 0 严格递增
    assert [c.idx for c in meta.chapters] == list(range(meta.total_chapters))


def test_parse_chapter_text_not_empty(sample_novel_path):
    meta = parse(sample_novel_path)
    for ch in meta.chapters:
        assert ch.char_count > 50, f"chapter {ch.idx} too short"


def test_parse_book_id_stable(sample_novel_path):
    a = parse(sample_novel_path)
    b = parse(sample_novel_path)
    assert a.book_id == b.book_id
