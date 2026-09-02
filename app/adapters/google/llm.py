"""Gemini API アダプタ（LLM_PROVIDER=gemini + GOOGLE_API_KEY で有効）。

依頼解釈・候補比較の説明文・調整文の生成に使う。失敗時は必ず stub 実装に
フォールバックし、LLM の不調が機能停止に波及しないようにする。
"""

from __future__ import annotations

import json
import logging

from app.adapters.mock.llm import StubLlmAdapter
from app.ports.llm import LlmPort

logger = logging.getLogger(__name__)

PARSE_INSTRUCTION = """あなたは会議調整の依頼文を構造化するパーサです。
入力の日本語から次のJSONだけを出力してください（説明文は不要）。
{
  "starts_at": "ISO8601（相対表現は現在時刻から解決。不明なら省略）",
  "participant_names": ["氏名"],
  "headcount": 整数,
  "equipment": ["プロジェクタ" | "ホワイトボード" | "モニタ" | "Web会議設備" | "電源"],
  "duration_minutes": 整数,
  "policy": "sum" | "minimax" | "cost"
}
読み取れない項目はキーごと省略してください。"""


class GeminiLlmAdapter(LlmPort):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3.7-flash") -> None:
        from google import genai  # 遅延importでキー未設定環境への影響を避ける

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._fallback = StubLlmAdapter()

    async def parse_request(self, text: str) -> dict:
        from datetime import datetime

        rule_based = await self._fallback.parse_request(text)
        try:
            resp = await self._client.aio.models.generate_content(
                model=self._model,
                contents=(
                    f"{PARSE_INSTRUCTION}\n\n"
                    f"現在時刻: {datetime.now().isoformat()}\n"
                    f"依頼文: {text}"
                ),
                config={"response_mime_type": "application/json"},
            )
            parsed = json.loads(resp.text)
            if not isinstance(parsed, dict):
                raise ValueError("dict ではありません")
            # ルールベースの結果を土台に、LLM の抽出結果で上書きする
            return {**rule_based, **{k: v for k, v in parsed.items() if v not in (None, [], "")}}
        except Exception:  # noqa: BLE001 — LLM 不調は機能停止にしない
            logger.warning("Gemini parse_request failed; falling back", exc_info=True)
            return rule_based

    async def explain(self, prompt: str, *, fallback: str) -> str:
        try:
            resp = await self._client.aio.models.generate_content(
                model=self._model, contents=prompt
            )
            text = (resp.text or "").strip()
            return text or fallback
        except Exception:  # noqa: BLE001
            logger.warning("Gemini explain failed; falling back", exc_info=True)
            return fallback
