"""自然文の解釈・説明文生成のポート。

既定は決定的な stub 実装で、GOOGLE_API_KEY があれば Gemini に差し替わる。
LLM が無くても全機能が動くように、呼び出し側は必ずフォールバックを持つ。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LlmPort(ABC):
    name: str = "llm"

    @abstractmethod
    async def parse_request(self, text: str) -> dict:
        """依頼文から {starts_at, participant_names, headcount, equipment,
        duration_minutes, policy} を抽出する。抽出できない項目は省く。"""

    @abstractmethod
    async def explain(self, prompt: str, *, fallback: str) -> str:
        """候補比較の説明文・調整文を生成する。失敗時は fallback を返す。"""
