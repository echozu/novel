"""知识图谱构建：把 NovelAnalysis 转换为 Cypher + JSON 双格式。

设计：
- 不强依赖 Neo4j（无 driver 也能产 JSON / Cypher 文件）
- ``write_neo4j=True`` 才连接 bolt 实际写入
- graph.json 用 Cytoscape.js 兼容格式，便于前端渲染
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..schema import NovelAnalysis


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_\u4e00-\u9fff]+")


def safe_id(*parts: str) -> str:
    raw = "_".join(p for p in parts if p)
    return _SAFE_ID_RE.sub("_", raw).strip("_")


def _esc_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class GraphArtifact:
    cypher: str
    elements: dict[str, list[dict[str, Any]]]  # cytoscape.js: {nodes, edges}


@dataclass
class KnowledgeGraphBuilder:
    write_neo4j: bool = False
    neo4j_uri: Optional[str] = None
    neo4j_user: Optional[str] = None
    neo4j_password: Optional[str] = None

    def build(self, analysis: NovelAnalysis) -> GraphArtifact:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        cypher_lines: list[str] = []

        book_id = safe_id("book", analysis.meta.book_id)
        nodes.append(
            {
                "data": {
                    "id": book_id,
                    "label": "Book",
                    "name": analysis.meta.title,
                    "genre": analysis.meta.genre,
                    "total_chapters": analysis.meta.total_chapters,
                }
            }
        )
        cypher_lines.append(
            f"MERGE (b:Book {{id:'{book_id}'}}) "
            f"SET b.title='{_esc_str(analysis.meta.title)}', "
            f"b.genre='{analysis.meta.genre}', "
            f"b.total_chapters={analysis.meta.total_chapters};"
        )

        # ---------- Chapters ----------
        chapter_id_map: dict[int, str] = {}
        for ch in analysis.meta.chapters:
            cid = safe_id("ch", str(analysis.meta.book_id), f"{ch.idx:05d}")
            chapter_id_map[ch.idx] = cid
            nodes.append(
                {
                    "data": {
                        "id": cid,
                        "label": "Chapter",
                        "name": ch.title or f"第{ch.idx+1}章",
                        "idx": ch.idx,
                        "book_id": book_id,
                    }
                }
            )
            cypher_lines.append(
                f"MERGE (c:Chapter {{id:'{cid}'}}) "
                f"SET c.title='{_esc_str(ch.title)}', c.idx={ch.idx}, c.book_id='{book_id}';"
            )
            cypher_lines.append(
                f"MATCH (b:Book {{id:'{book_id}'}}), (c:Chapter {{id:'{cid}'}}) "
                f"MERGE (b)-[:CONTAINS_CHAPTER {{order:{ch.idx}}}]->(c);"
            )
            edges.append({"data": {"source": book_id, "target": cid, "label": "CONTAINS_CHAPTER"}})

        # ---------- Characters ----------
        character_id_map: dict[str, str] = {}
        for ch_p in analysis.characters:
            cid = safe_id("char", ch_p.character_id or ch_p.name)
            character_id_map[ch_p.name] = cid
            for alias in ch_p.aliases:
                character_id_map.setdefault(alias, cid)
            nodes.append(
                {
                    "data": {
                        "id": cid,
                        "label": "Character",
                        "name": ch_p.name,
                        "role": ch_p.role,
                        "one_liner": ch_p.one_liner,
                        "motivation": ch_p.motivation,
                        "book_id": book_id,
                    }
                }
            )
            cypher_lines.append(
                f"MERGE (p:Character {{id:'{cid}'}}) "
                f"SET p.name='{_esc_str(ch_p.name)}', p.role='{ch_p.role}', "
                f"p.one_liner='{_esc_str(ch_p.one_liner)}', "
                f"p.motivation='{_esc_str(ch_p.motivation)}', p.book_id='{book_id}';"
            )

            # APPEARS_IN
            for chapter_idx in ch_p.appearance_chapters[:80]:
                target = chapter_id_map.get(chapter_idx)
                if not target:
                    continue
                edges.append(
                    {"data": {"source": cid, "target": target, "label": "APPEARS_IN"}}
                )
                cypher_lines.append(
                    f"MATCH (p:Character {{id:'{cid}'}}), (c:Chapter {{id:'{target}'}}) "
                    f"MERGE (p)-[:APPEARS_IN]->(c);"
                )

            # EVOLVES_TO 链 — 弧光状态点
            arc_sorted = sorted(ch_p.arc, key=lambda a: a.chapter_idx)
            for a, b in zip(arc_sorted, arc_sorted[1:]):
                if a.chapter_idx in chapter_id_map and b.chapter_idx in chapter_id_map:
                    src_ch = chapter_id_map[a.chapter_idx]
                    dst_ch = chapter_id_map[b.chapter_idx]
                    edges.append(
                        {
                            "data": {
                                "source": src_ch,
                                "target": dst_ch,
                                "label": "EVOLVES_TO",
                                "character": ch_p.name,
                                "from_stage": a.stage,
                                "to_stage": b.stage,
                                "note": b.psychological_change,
                            }
                        }
                    )

        # 现在再连接关系（需要 character_id_map 已建）
        for ch_p in analysis.characters:
            cid = character_id_map.get(ch_p.name)
            if not cid:
                continue
            for rel in ch_p.relationships:
                target_name = rel.get("target") or ""
                target_id = character_id_map.get(target_name)
                if not target_id:
                    continue
                rel_type = (rel.get("type") or "ally").upper()
                edges.append(
                    {
                        "data": {
                            "source": cid,
                            "target": target_id,
                            "label": "RELATIONSHIP_WITH",
                            "type": rel.get("type", "ally"),
                            "evolution": rel.get("evolution", []),
                        }
                    }
                )
                cypher_lines.append(
                    f"MATCH (a:Character {{id:'{cid}'}}), (b:Character {{id:'{target_id}'}}) "
                    f"MERGE (a)-[r:RELATIONSHIP_WITH {{type:'{rel.get('type','ally')}'}}]->(b);"
                )

        # ---------- Locations + Scenes ----------
        location_id_map: dict[str, str] = {}
        for ch_a in analysis.chapters:
            for sc in ch_a.scenes:
                loc = sc.location.strip()
                if not loc:
                    continue
                if loc not in location_id_map:
                    lid = safe_id("loc", loc)
                    location_id_map[loc] = lid
                    nodes.append(
                        {
                            "data": {
                                "id": lid,
                                "label": "Location",
                                "name": loc,
                                "book_id": book_id,
                            }
                        }
                    )
                    cypher_lines.append(
                        f"MERGE (l:Location {{id:'{lid}'}}) "
                        f"SET l.name='{_esc_str(loc)}', l.book_id='{book_id}';"
                    )
                lid = location_id_map[loc]
                ch_target = chapter_id_map.get(ch_a.chapter_idx)
                if ch_target:
                    edges.append(
                        {"data": {"source": ch_target, "target": lid, "label": "SET_IN"}}
                    )

        # ---------- Plotline events ----------
        event_id_counter = 0
        for line in analysis.plotlines:
            line_kind = line.line.value if hasattr(line.line, "value") else str(line.line)
            for e in line.events:
                event_id_counter += 1
                eid = safe_id("evt", str(event_id_counter), str(e.chapter_idx))
                nodes.append(
                    {
                        "data": {
                            "id": eid,
                            "label": "Event",
                            "name": e.title,
                            "summary": e.summary,
                            "line": line_kind,
                            "chapter_idx": e.chapter_idx,
                            "book_id": book_id,
                        }
                    }
                )
                cypher_lines.append(
                    f"MERGE (e:Event {{id:'{eid}'}}) "
                    f"SET e.title='{_esc_str(e.title)}', e.line='{line_kind}', "
                    f"e.chapter_idx={e.chapter_idx}, e.book_id='{book_id}';"
                )
                # 连接到所属章节
                if e.chapter_idx in chapter_id_map:
                    edges.append(
                        {
                            "data": {
                                "source": chapter_id_map[e.chapter_idx],
                                "target": eid,
                                "label": "BELONGS_TO_LINE",
                                "line": line_kind,
                            }
                        }
                    )
                # 连接相关角色
                for cname in e.characters:
                    target = character_id_map.get(cname)
                    if target:
                        edges.append(
                            {
                                "data": {
                                    "source": target,
                                    "target": eid,
                                    "label": "PARTICIPATES_IN",
                                }
                            }
                        )

        # ---------- Tropes ----------
        for t in analysis.top_tropes[:50]:
            tid = safe_id("trope", t.trope_id)
            nodes.append(
                {
                    "data": {
                        "id": tid,
                        "label": "Trope",
                        "name": t.trope_name,
                        "trope_id": t.trope_id,
                        "book_id": book_id,
                    }
                }
            )
            cypher_lines.append(
                f"MERGE (t:Trope {{id:'{tid}'}}) "
                f"SET t.name='{_esc_str(t.trope_name)}', t.trope_id='{t.trope_id}';"
            )
            for chap_idx in t.evidence_chapter[:5]:
                if chap_idx in chapter_id_map:
                    edges.append(
                        {
                            "data": {
                                "source": chapter_id_map[chap_idx],
                                "target": tid,
                                "label": "USES_TROPE",
                            }
                        }
                    )

        # ---------- Top quotes ----------
        for i, q in enumerate(analysis.top_quotes[:30]):
            qid = safe_id("quote", str(i))
            nodes.append(
                {
                    "data": {
                        "id": qid,
                        "label": "Quote",
                        "name": (q.text[:30] + "...") if len(q.text) > 30 else q.text,
                        "text": q.text,
                        "speaker": q.speaker or "",
                        "why_good": q.why_good,
                        "book_id": book_id,
                    }
                }
            )
            cypher_lines.append(
                f"MERGE (q:Quote {{id:'{qid}'}}) "
                f"SET q.text='{_esc_str(q.text)}', q.speaker='{_esc_str(q.speaker or '')}';"
            )
            for chap_idx in q.evidence_chapter[:2]:
                if chap_idx in chapter_id_map:
                    edges.append(
                        {
                            "data": {
                                "source": chapter_id_map[chap_idx],
                                "target": qid,
                                "label": "CONTAINS_QUOTE",
                            }
                        }
                    )

        artifact = GraphArtifact(
            cypher="\n".join(cypher_lines),
            elements={"nodes": nodes, "edges": edges},
        )

        if self.write_neo4j:
            self._write_neo4j(artifact)
        return artifact

    def write_files(self, artifact: GraphArtifact, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "knowledge_graph.cypher").write_text(artifact.cypher, encoding="utf-8")
        (out_dir / "graph.json").write_text(
            json.dumps(artifact.elements, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_neo4j(self, artifact: GraphArtifact) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError:
            return
        if not (self.neo4j_uri and self.neo4j_user and self.neo4j_password):
            return
        driver = GraphDatabase.driver(self.neo4j_uri, auth=(self.neo4j_user, self.neo4j_password))
        try:
            schema_path = Path(__file__).with_name("schema.cypher")
            schema = schema_path.read_text(encoding="utf-8")
            with driver.session() as session:
                for stmt in schema.split(";"):
                    if stmt.strip():
                        session.run(stmt)
                for stmt in artifact.cypher.split(";"):
                    if stmt.strip():
                        session.run(stmt)
        finally:
            driver.close()
