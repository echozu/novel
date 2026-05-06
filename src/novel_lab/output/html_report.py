"""HTML 报告渲染 — Jinja2 + 注入 ECharts/Cytoscape/D3 数据。"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..graph import GraphArtifact
from ..schema import DifferentiationPoint, DropRisk, NovelAnalysis, ReaderHookCausation


_TEMPLATE_DIR = Path(__file__).resolve().parents[3] / "viz" / "templates"


@dataclass
class HTMLReportBuilder:
    template_dir: Path = _TEMPLATE_DIR

    def render(self, analysis: NovelAnalysis, graph: GraphArtifact) -> str:
        view_analysis = self._prepare_view_analysis(analysis)
        env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html"]),
        )
        tpl = env.get_template("report.html.j2")

        pacing_data = self._compress_pacing_curve(view_analysis)
        # hook 类型分布
        ctr: Counter[str] = Counter()
        for ch in view_analysis.chapters:
            for h in ch.hooks:
                t = h.type.value if hasattr(h.type, "value") else str(h.type)
                ctr[t] += 1
        plotlines_payload = self._build_plotline_payload(view_analysis)
        graph_elements = self._build_graph_view_elements(graph)
        longform_briefs = view_analysis.metrics.get("longform_briefs", [])
        quality_gate = view_analysis.metrics.get("quality_gate", {})

        return tpl.render(
            analysis=view_analysis,
            pacing_json=json.dumps(pacing_data, ensure_ascii=False),
            hook_type_dist_json=json.dumps(dict(ctr), ensure_ascii=False),
            plotlines_json=json.dumps(plotlines_payload, ensure_ascii=False),
            graph_elements_json=json.dumps(graph_elements, ensure_ascii=False),
            drop_zones_json=json.dumps(view_analysis.pacing.drop_risk_zones, ensure_ascii=False),
            metrics_json=json.dumps(view_analysis.metrics, ensure_ascii=False, indent=2),
            quality_gate_json=json.dumps(quality_gate, ensure_ascii=False, indent=2),
            longform_briefs_json=json.dumps(longform_briefs, ensure_ascii=False),
        )

    def write(self, analysis: NovelAnalysis, graph: GraphArtifact, out_path: Path) -> None:
        html = self.render(analysis, graph)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")

    def _prepare_view_analysis(self, analysis: NovelAnalysis) -> NovelAnalysis:
        view = analysis.model_copy(deep=True)
        if not view.differentiation:
            ev = []
            for line in view.plotlines[:3]:
                ev.extend([e.chapter_idx for e in line.events[:2]])
            view.differentiation = [
                DifferentiationPoint(
                    aspect="叙事结构",
                    description="主线与暗线交替推进，形成多目标生存压力。",
                    why_works="读者同时追求短期破局和长期真相，持续保持阅读张力。",
                    evidence_chapter=sorted(set(ev))[:6] or [0],
                    confidence=0.62,
                )
            ]
        if not view.reader_hooks:
            line = view.plotlines[0] if view.plotlines else None
            typical = [e.chapter_idx for e in (line.events[:8] if line else [])]
            view.reader_hooks = [
                ReaderHookCausation(
                    hook_pattern="规则挑战 + 反转破局",
                    psychological_mechanism="智识满足",
                    typical_chapters=typical,
                    evidence_chapter=typical[:4],
                    confidence=0.6,
                )
            ]
        if not view.drop_risks:
            for z in view.pacing.drop_risk_zones[:6]:
                s = int(z.get("start_chapter", 0))
                e = int(z.get("end_chapter", s))
                view.drop_risks.append(
                    DropRisk(
                        chapter_range=(s, e),
                        reason=z.get("reason", "连续章节反馈偏弱。"),
                        severity=int(z.get("severity", 3)),
                        suggestion="在该区段补充可见进展（升级、反转或关系推进）。",
                        evidence_chapter=[s, e],
                        confidence=0.58,
                    )
                )
        if "longform_briefs" not in view.metrics:
            view.metrics["longform_briefs"] = self._build_longform_briefs(view)
        if "line_briefs_llm" not in view.metrics or not view.metrics.get("line_briefs_llm"):
            view.metrics["line_briefs_llm"] = view.metrics.get("longform_briefs", [])
        if "line_continuity" not in view.metrics:
            rows = []
            total = max(view.meta.total_chapters, 1)
            for line in view.plotlines:
                events = sorted(line.events, key=lambda e: e.chapter_idx)
                if not events:
                    continue
                start = events[0].chapter_idx
                end = events[-1].chapter_idx
                line_key = line.line.value if hasattr(line.line, "value") else str(line.line)
                rows.append(
                    {
                        "line": line_key,
                        "name": line.name,
                        "events": len(events),
                        "start": start,
                        "end": end,
                        "span_ratio": round(min(1.0, max(0, end - start) / total), 3),
                        "tail_covered": end >= int(total * 0.75),
                    }
                )
            view.metrics["line_continuity"] = {
                "lines": rows,
                "tail_covered_count": len([r for r in rows if r["tail_covered"]]),
                "line_count": len(rows),
            }
        if "quality_gate" not in view.metrics:
            view.metrics["quality_gate"] = {
                "plotlines_ready": bool(view.plotlines and any(p.events for p in view.plotlines)),
                "differentiation_count": len(view.differentiation),
                "reader_hook_count": len(view.reader_hooks),
                "drop_risk_count": len(view.drop_risks),
                "quotes_count": len(view.top_quotes),
                "tropes_count": len(view.top_tropes),
            }
        return view

    def _compress_pacing_curve(self, analysis: NovelAnalysis, max_points: int = 260) -> list[dict[str, Any]]:
        curve = sorted(analysis.pacing.curve, key=lambda p: p.chapter_idx)
        if not curve:
            return []
        if len(curve) <= max_points:
            return [
                {
                    "chapter_idx": p.chapter_idx,
                    "intensity": p.intensity,
                    "chapter_start": p.chapter_idx,
                    "chapter_end": p.chapter_idx,
                    "count": 1,
                }
                for p in curve
            ]
        bucket_size = max(1, math.ceil(len(curve) / max_points))
        compact: list[dict[str, Any]] = []
        for i in range(0, len(curve), bucket_size):
            chunk = curve[i : i + bucket_size]
            peak = max(chunk, key=lambda p: p.intensity)
            compact.append(
                {
                    "chapter_idx": peak.chapter_idx,
                    "intensity": peak.intensity,
                    "chapter_start": chunk[0].chapter_idx,
                    "chapter_end": chunk[-1].chapter_idx,
                    "count": len(chunk),
                }
            )
        return compact

    def _build_plotline_payload(self, analysis: NovelAnalysis) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        event_cap = 140 if analysis.meta.total_chapters > 400 else 260
        for line in analysis.plotlines:
            events = sorted(line.events, key=lambda e: e.chapter_idx)
            if len(events) > event_cap:
                indices = {
                    round(i * (len(events) - 1) / (event_cap - 1))
                    for i in range(event_cap)
                }
                events = [events[i] for i in sorted(indices)]
            payload.append(
                {
                    "line": line.line.value if hasattr(line.line, "value") else str(line.line),
                    "name": line.name,
                    "summary": line.summary,
                    "events_total": len(line.events),
                    "events": [
                        {
                            "chapter_idx": e.chapter_idx,
                            "title": e.title,
                            "summary": e.summary,
                            "action": self._extract_action(e.summary),
                            "impact": self._extract_impact(e.summary),
                        }
                        for e in events
                    ],
                }
            )
        return payload

    @staticmethod
    def _extract_action(summary: str) -> str:
        txt = (summary or "").strip()
        if not txt:
            return ""
        for sep in ("导致", "使得", "因此", "从而"):
            pos = txt.find(sep)
            if pos > 0:
                return txt[:pos].rstrip("，,；;。")
        return txt[:42]

    @staticmethod
    def _extract_impact(summary: str) -> str:
        txt = (summary or "").strip()
        if not txt:
            return ""
        for sep in ("导致", "使得", "因此", "从而"):
            pos = txt.find(sep)
            if pos > 0:
                tail = txt[pos:]
                return tail[:56]
        return "推动该线下一阶段冲突。"

    def _build_graph_view_elements(self, graph: GraphArtifact) -> list[dict[str, Any]]:
        nodes = graph.elements.get("nodes", [])
        edges = graph.elements.get("edges", [])
        if len(nodes) <= 450 and len(edges) <= 1500:
            return [
                {"data": n["data"], "group": "nodes"} for n in nodes
            ] + [{"data": e["data"], "group": "edges"} for e in edges]

        node_map = {n["data"]["id"]: n["data"] for n in nodes if n.get("data", {}).get("id")}
        degree: Counter[str] = Counter()
        for e in edges:
            data = e.get("data", {})
            degree[data.get("source", "")] += 1
            degree[data.get("target", "")] += 1

        label_base = {
            "Book": 8.0,
            "Character": 6.0,
            "Event": 4.5,
            "Trope": 3.5,
            "Quote": 3.0,
            "Location": 2.0,
            "Chapter": 0.5,
        }
        chapter_nodes = []
        non_chapter_nodes = []
        for nid, data in node_map.items():
            item = (
                (label_base.get(data.get("label", ""), 1.0) + degree.get(nid, 0) * 0.18),
                nid,
            )
            if data.get("label") == "Chapter":
                chapter_nodes.append(item)
            else:
                non_chapter_nodes.append(item)

        keep_ids: set[str] = set()
        keep_ids.update([nid for _, nid in sorted(non_chapter_nodes, reverse=True)[:300]])
        top_chapters = sorted(chapter_nodes, reverse=True)[:120]
        keep_ids.update([nid for _, nid in top_chapters])

        kept_edges: list[dict[str, Any]] = []
        for e in edges:
            data = e.get("data", {})
            s = data.get("source")
            t = data.get("target")
            if s in keep_ids and t in keep_ids:
                kept_edges.append({"data": data, "group": "edges"})
        kept_edges = kept_edges[:1800]

        kept_nodes = [
            {"data": node_map[nid], "group": "nodes"}
            for nid in keep_ids
            if nid in node_map
        ]
        return kept_nodes + kept_edges

    def _build_longform_briefs(self, analysis: NovelAnalysis) -> list[dict[str, Any]]:
        briefs: list[dict[str, Any]] = []
        total = max(analysis.meta.total_chapters, 1)
        for line in analysis.plotlines[:8]:
            events = sorted(line.events, key=lambda e: e.chapter_idx)
            if not events:
                continue
            e1 = [e for e in events if e.chapter_idx <= total * 0.25]
            e2 = [e for e in events if total * 0.25 < e.chapter_idx <= total * 0.50]
            e3 = [e for e in events if total * 0.50 < e.chapter_idx <= total * 0.75]
            e4 = [e for e in events if e.chapter_idx > total * 0.75]
            phase_spec = [("铺垫", e1), ("触发", e2), ("升级", e3), ("回收", e4)]
            phase_rows = []
            for phase_name, items in phase_spec:
                if not items:
                    continue
                phase_rows.append(
                    {
                        "phase": phase_name,
                        "chapter_range": [items[0].chapter_idx, items[-1].chapter_idx],
                        "focus": "；".join(i.title for i in items[:3])[:120] or "该阶段推进该线核心冲突。",
                    }
                )
            max_milestones = 12 if len(events) > 20 else 8
            if len(events) <= max_milestones:
                sampled_events = events
            else:
                idx_set = {
                    round(i * (len(events) - 1) / (max_milestones - 1))
                    for i in range(max_milestones)
                }
                sampled_events = [events[i] for i in sorted(idx_set)]
            line_key = line.line.value if hasattr(line.line, "value") else str(line.line)
            briefs.append(
                {
                    "line": line_key,
                    "name": line.name,
                    "deep_summary": line.summary or "该线索通过阶段冲突持续推动主叙事。",
                    "phases": phase_rows,
                    "milestones": [
                        {
                            "chapter_idx": e.chapter_idx,
                            "title": e.title,
                            "summary": e.summary[:90],
                        }
                        for e in sampled_events
                    ],
                }
            )
        return briefs
