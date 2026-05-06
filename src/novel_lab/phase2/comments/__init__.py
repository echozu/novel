"""读者评论挖掘 → 章节对齐 → Layer3 反向校准。

合规：仅用于学习/分析，不分发原文；遵守 robots.txt、限速、UA 轮换。
依赖：``pip install -e ".[phase2]"`` 才能跑爬虫与 BERT。
"""

from .schema import RawComment, ChapterCommentDigest  # noqa: F401
