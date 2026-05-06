"""评论聚类 — 优先 BERT (sentence-transformers) + KMeans，缺依赖时退化为 TF-IDF + KMeans。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

from .clean import naive_tokenize
from .schema import CommentCluster, RawComment


_POS_KW = {"爽", "哈哈", "牛", "炸", "舒服", "破防", "代入", "上头", "刺激", "解气", "燃", "顶"}
_NEG_KW = {"无聊", "拖", "水", "降智", "崩", "弃", "尬", "智障", "套路", "硬", "强行"}


def _sentiment(text: str) -> str:
    p = sum(1 for k in _POS_KW if k in text)
    n = sum(1 for k in _NEG_KW if k in text)
    if p > n + 1:
        return "pos"
    if n > p + 1:
        return "neg"
    return "neu"


def cluster_comments(
    comments: list[RawComment],
    *,
    n_clusters: int = 12,
    use_bert: bool = True,
    embed_model: str = "BAAI/bge-small-zh-v1.5",
) -> list[CommentCluster]:
    if not comments:
        return []

    texts = [c.text for c in comments]
    embeddings = None
    if use_bert:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]

            model = SentenceTransformer(embed_model)
            embeddings = model.encode(texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True)
        except ImportError:
            embeddings = None

    if embeddings is None:
        # TF-IDF 兜底
        try:
            from sklearn.cluster import KMeans
            from sklearn.feature_extraction.text import TfidfVectorizer

            vec = TfidfVectorizer(
                tokenizer=naive_tokenize,
                token_pattern=None,
                max_features=4096,
            )
            X = vec.fit_transform(texts)
            km = KMeans(n_clusters=min(n_clusters, max(2, len(comments) // 8 or 2)),
                        n_init=4, random_state=42)
            labels = km.fit_predict(X)
        except ImportError:
            # 极简兜底：哈希分组
            labels = [hash(t) % n_clusters for t in texts]
    else:
        from sklearn.cluster import KMeans  # 总会装上

        km = KMeans(
            n_clusters=min(n_clusters, max(2, len(comments) // 8 or 2)),
            n_init=4,
            random_state=42,
        )
        labels = km.fit_predict(embeddings)

    bucket: dict[int, list[RawComment]] = defaultdict(list)
    for label, c in zip(labels, comments):
        bucket[int(label)].append(c)

    out: list[CommentCluster] = []
    total = len(comments)
    for cid, members in bucket.items():
        joined = " ".join(m.text for m in members)
        sent = _sentiment(joined)
        kw_ctr: Counter[str] = Counter()
        for m in members:
            for tok in naive_tokenize(m.text):
                if len(tok) >= 2:
                    kw_ctr[tok] += 1
        kw = [w for w, _ in kw_ctr.most_common(10)]
        chap_dist: Counter[int] = Counter()
        for m in members:
            if m.chapter_idx is not None:
                chap_dist[m.chapter_idx] += 1
        sample = sorted(members, key=lambda x: -x.likes)[:3]
        label = ""
        if kw:
            label = (
                ("【正面】" if sent == "pos" else "【负面】" if sent == "neg" else "【中性】")
                + "/".join(kw[:3])
            )
        out.append(
            CommentCluster(
                cluster_id=cid,
                label=label,
                sentiment=sent,
                keywords=kw,
                sample_comments=[s.text for s in sample],
                chapter_idx_distribution=dict(chap_dist),
                weight=round(len(members) / total, 4),
            )
        )
    out.sort(key=lambda x: -x.weight)
    return out
