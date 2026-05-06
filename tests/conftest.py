"""共享 fixture：sample 小说路径 + Fake LLM Router。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from novel_lab.llm.router import LLMResponse


SAMPLE_PATH = Path(__file__).parent / "fixtures" / "sample_novel.txt"


@pytest.fixture()
def sample_novel_path() -> Path:
    return SAMPLE_PATH


# ----- Fake LLM Router -----------------------------------------------


class FakeRouter:
    """根据 system prompt 中的关键短语返回 canned JSON，不依赖网络。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.total_cost = 0.0
        self._total_in = 0
        self._total_out = 0

    def model_for(self, role: str) -> str:
        return f"fake-{role}"

    @property
    def total_tokens(self) -> tuple[int, int]:
        return self._total_in, self._total_out

    async def complete(self, messages, *, role="map", json_mode=True, temperature=0.2,
                       max_tokens=1024, system=None, cache=True) -> LLMResponse:
        self.calls.append({"role": role, "system": system, "messages": messages})
        sys_text = system or ""
        # 用各 prompt 独有的标题做精确路由，避免内容关键字误匹配
        if "Layer1 章节级 Map Agent" in sys_text:
            payload = self._fake_map(messages)
        elif "Layer2 人物弧光追踪 Agent" in sys_text:
            payload = self._fake_arc()
        elif "Layer2 主线 / 三暗线分离 Agent" in sys_text:
            payload = self._fake_plotline()
        elif "Layer2 文风指纹 Agent" in sys_text:
            payload = self._fake_style()
        elif "Layer3 差异点提炼 Agent" in sys_text:
            payload = self._fake_diff()
        elif "Layer3 读者爽点归因 Agent" in sys_text:
            payload = self._fake_hook()
        elif "Layer3 弃书风险点 Agent" in sys_text:
            payload = self._fake_risk()
        elif "Self-Critique Agent" in sys_text:
            payload = self._fake_critic()
        elif "Layer4 创作宪法" in sys_text:
            return LLMResponse(text="# 创作宪法\n\n这是一个 fake 宪法。\n",
                               tokens_in=10, tokens_out=10, model="fake")
        else:
            payload = {}
        text = json.dumps(payload, ensure_ascii=False)
        self._total_in += 100
        self._total_out += 100
        return LLMResponse(text=text, tokens_in=100, tokens_out=100, model="fake")

    # canned data ----------------------------------------------------

    @staticmethod
    def _fake_map(messages):
        # 从 user 消息里抽 chapter_idx 兜底
        idx = 0
        for m in messages:
            content = m.get("content", "")
            if "chapter_idx:" in content:
                line = next((l for l in content.splitlines() if "chapter_idx:" in l), "")
                try:
                    idx = int(line.split(":", 1)[1].strip())
                except Exception:
                    idx = 0
                break
        return {
            "chapter_idx": idx,
            "summary": f"第{idx}章 fake 摘要：少年下山遇敌，初露剑锋。",
            "mentioned_characters": [
                {
                    "name": "林云", "aliases": ["少年"], "role_hint": "主角",
                    "actions": ["下山", "出剑"], "emotional_state": "桀骜",
                    "relationship_updates": [],
                    "evidence_chapter": [idx], "confidence": 0.9
                }
            ],
            "scenes": [
                {"location": "山下", "time_clue": "黄昏", "summary": "林云走出山门",
                 "participants": ["林云"], "evidence_chapter": [idx], "confidence": 0.8}
            ],
            "hooks": [
                {"type": "cliffhanger", "intensity": 4 if idx % 2 == 0 else 2,
                 "summary": "章末大反转", "snippet": "他知道，真正的故事，才刚刚开始。",
                 "position": "ending", "evidence_chapter": [idx], "confidence": 0.85},
                {"type": "face_slap", "intensity": 3, "summary": "打脸黑衣人",
                 "snippet": "三息倒下", "position": "middle",
                 "evidence_chapter": [idx], "confidence": 0.8},
            ],
            "quotes": [
                {"text": "这世道，真是该有人收拾了。", "speaker": "林云",
                 "why_good": "凝练 + 反差", "evidence_chapter": [idx], "confidence": 0.9}
            ],
            "tropes": [
                {"trope_id": "revenge_arc", "trope_name": "复仇线",
                 "instance_summary": "林家被灭门，少年下山复仇",
                 "evidence_chapter": [idx], "confidence": 0.85}
            ]
        }

    @staticmethod
    def _fake_arc():
        return {
            "characters": [
                {
                    "character_id": "char_林云",
                    "name": "林云",
                    "aliases": ["少年"],
                    "role": "protagonist",
                    "one_liner": "背负灭门血仇的少年剑客",
                    "appearance_chapters": [0, 1, 2, 3, 4, 5, 6, 7],
                    "arc": [
                        {"stage": "initial", "chapter_idx": 0,
                         "state_summary": "山门下山，背债前行", "psychological_change": "桀骜"},
                        {"stage": "catalyst", "chapter_idx": 2,
                         "state_summary": "镇上初遇秦家", "psychological_change": "杀意起"},
                        {"stage": "turn_50", "chapter_idx": 4,
                         "state_summary": "灭秦府", "psychological_change": "心境破"},
                        {"stage": "final", "chapter_idx": 7,
                         "state_summary": "再下山去东海", "psychological_change": "归来再行"}
                    ],
                    "relationships": [
                        {"target": "苏婉", "type": "love",
                         "evolution": [{"chapter": 0, "note": "师妹相送"}]}
                    ],
                    "motivation": "为林家复仇并寻剑道极致",
                    "flaws": ["太执于过去", "杀心过盛"],
                    "evidence_chapter": [0, 4, 7],
                    "confidence": 0.85
                }
            ]
        }

    @staticmethod
    def _fake_plotline():
        return {
            "plotlines": [
                {
                    "line": "main", "name": "复仇主线",
                    "summary": "林云为林家灭门复仇，渐入剑道。",
                    "events": [
                        {"chapter_idx": 0, "title": "下山", "summary": "少年走出山门",
                         "line": "main", "characters": ["林云"],
                         "evidence_chapter": [0], "confidence": 0.9},
                        {"chapter_idx": 4, "title": "灭秦府",
                         "summary": "林云一夜灭秦家，了断旧怨", "line": "main",
                         "characters": ["林云"],
                         "evidence_chapter": [4], "confidence": 0.9},
                        {"chapter_idx": 7, "title": "再下山",
                         "summary": "前往东海再求剑道", "line": "main",
                         "characters": ["林云", "苏婉"],
                         "evidence_chapter": [7], "confidence": 0.9}
                    ],
                    "intersections": [{"chapter_idx": 4, "with_line": "emotional",
                                       "note": "复仇与父母情感同时落点"}]
                },
                {
                    "line": "emotional", "name": "师妹之情",
                    "summary": "林云与苏婉的情感伏笔。",
                    "events": [
                        {"chapter_idx": 0, "title": "师妹相送",
                         "summary": "苏婉拉衣角不舍", "line": "emotional",
                         "characters": ["苏婉", "林云"],
                         "evidence_chapter": [0], "confidence": 0.85},
                        {"chapter_idx": 7, "title": "玉佩为约",
                         "summary": "苏婉戴玉佩约其归来", "line": "emotional",
                         "characters": ["苏婉", "林云"],
                         "evidence_chapter": [7], "confidence": 0.85}
                    ],
                    "intersections": []
                }
            ]
        }

    @staticmethod
    def _fake_style():
        return {
            "pov": "第三人称限知",
            "tense": "过去时",
            "avg_sentence_length": 18.0,
            "dialog_ratio": 0.45,
            "description_density": "适中",
            "rhetoric_devices": ["比喻", "反衬"],
            "tone_keywords": ["冷峻", "桀骜", "热血"],
            "signature_phrases": ["不知怎的", "这世道"],
            "sample_paragraphs": ["山风很大，吹乱了少年的衣袂。他没有回头。"]
        }

    @staticmethod
    def _fake_diff():
        return {
            "differentiations": [
                {"aspect": "人设",
                 "description": "主角既桀骜又克制，杀人前先邀宾客离场",
                 "why_works": "强反差带来道德安全感与代入感",
                 "evidence_chapter": [4], "confidence": 0.8}
            ]
        }

    @staticmethod
    def _fake_hook():
        return {
            "patterns": [
                {"hook_pattern": "灭门复仇·一夜清算",
                 "psychological_mechanism": "公平感",
                 "typical_chapters": [4, 5],
                 "evidence_chapter": [4], "confidence": 0.85}
            ]
        }

    @staticmethod
    def _fake_risk():
        return {
            "risks": [
                {"chapter_range": [1, 1],
                 "reason": "镇上铺垫稍长，未直接抛出爽点",
                 "severity": 2,
                 "suggestion": "增加镇口大汉与林云的语锋交锋以提速",
                 "evidence_chapter": [1], "confidence": 0.7}
            ]
        }

    @staticmethod
    def _fake_critic():
        return {"critiques": [{"target_id": "diff_0", "pass_check": True, "issues": []}]}
