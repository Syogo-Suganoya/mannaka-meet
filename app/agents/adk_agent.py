"""ADK（Agent Development Kit）でのエージェント公開。

Orchestrator の各操作を FunctionTool として LlmAgent に渡し、自然言語の指示で
候補算出を駆動できるようにする。ADK / Gemini が使えない環境では
build_root_agent() が None を返し、REST API 側の機能はそのまま使える。
"""

from __future__ import annotations

import logging

from app.deps import Container
from app.domain.models import FairnessPolicy

logger = logging.getLogger(__name__)

INSTRUCTION = """あなたは「マンナカ」— N人会議の場所を公平に決めるエージェントです。

守ること:
- 場所を提案するときは、必ずどの公平性ポリシー（sum=合計移動時間最小 /
  minimax=最大負担の平準化 / cost=総運賃最小）で選んだかを明示する。
- 誰がどれだけ負担するかは名前と出発駅つきで示す。偏りを隠さない。
- どの場所にするかは決めない。候補と根拠を出すところまでが仕事。

手順: create_meeting で候補算出 → evaluate_candidates で基準を入れ替え →
compare_fairness_policies で基準ごとの差を示す。
"""


def build_tools(container: Container) -> list:
    """Orchestrator の操作を ADK ツールとして公開する。"""
    orchestrator = container.orchestrator

    async def create_meeting(names: list[str], stations: list[str], notes: str) -> dict:
        """参加者から候補地を算出する。

        Args:
            names: 参加者の名前（stations と同じ並び）。
            stations: 各参加者の出発駅。
            notes: 「いちばん遠い人の負担を減らしたい」などの希望条件。
        Returns:
            meeting_id と採用した基準。
        """
        meeting = await orchestrator.create_meeting(
            participants=list(zip(names, stations)), notes=notes
        )
        return {
            "meeting_id": meeting.meeting_id,
            "starts_at": meeting.request.starts_at.isoformat(),
            "participant_count": len(meeting.request.participants),
            "policy": meeting.policy.value if meeting.policy else None,
        }

    async def evaluate_candidates(meeting_id: str, policy: str) -> dict:
        """公平性ポリシーを指定して候補地を算出する。

        Args:
            meeting_id: 会議ID。
            policy: sum | minimax | cost。
        Returns:
            候補地の集計値（個人の内訳は含まない）。
        """
        meeting = await orchestrator.optimize(meeting_id, FairnessPolicy(policy))
        result = meeting.optimization
        return {
            "policy": result.policy.value,
            "policy_label": result.policy.label,
            "explanation": result.explanation,
            "candidates": [
                {
                    "rank": c.rank,
                    "area": c.area,
                    "station": c.station,
                    "total_minutes": c.total_minutes,
                    "max_minutes": c.max_minutes,
                    "total_fare_yen": c.total_fare_yen,
                    "breakdown": c.breakdown(
                        {p.uid: p.display_name for p in meeting.request.participants}
                    ),
                }
                for c in result.candidates
            ],
        }

    async def compare_fairness_policies(meeting_id: str) -> dict:
        """3つの公平性ポリシーそれぞれの最良候補を比較する。

        Args:
            meeting_id: 会議ID。
        Returns:
            ポリシーごとの最良候補と集計値。
        """
        return await orchestrator.policy_comparison(meeting_id)

    return [
        create_meeting,
        evaluate_candidates,
        compare_fairness_policies,
    ]


def build_root_agent(container: Container):
    """ADK の LlmAgent を組み立てる。使えない環境では None。"""
    settings = container.settings
    if settings.llm_provider != "gemini" or not settings.google_api_key:
        logger.info("ADK エージェントは無効（LLM_PROVIDER=gemini と GOOGLE_API_KEY が必要）")
        return None
    try:
        from google.adk.agents import LlmAgent

        return LlmAgent(
            name="mannaka_orchestrator",
            model=settings.gemini_model,
            description="N人会議の場所を公平性ポリシー付きで比較するエージェント",
            instruction=INSTRUCTION,
            tools=build_tools(container),
        )
    except Exception:  # noqa: BLE001 — ADK 不在でもサービスは動かす
        logger.warning("ADK エージェントの構築に失敗しました", exc_info=True)
        return None
