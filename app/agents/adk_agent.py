"""ADK（Agent Development Kit）でのエージェント公開。

Orchestrator の各操作を FunctionTool として LlmAgent に渡し、チャットからの
自然言語指示で一気通貫を駆動できるようにする。ADK / Gemini が使えない環境では
build_root_agent() が None を返し、REST API 側の機能はそのまま使える。

有料予約の確定はツールとして公開しない（承認ゲートを LLM に迂回させないため）。
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
- 誰がどこから来るかは絶対に開示しない。集計値と匿名の内訳だけを話す。
- 有料の会議室予約は主催者の承認が必要。あなたは承認できない。承認が必要な
  ときは金額と上限額を伝えて主催者に判断を仰ぐ。
- 当日の遅延対応は「提案」まで。時刻変更を勝手に確定しない。

手順: 依頼を interpret_request で構造化 → evaluate_candidates で候補算出 →
compare_fairness_policies で基準ごとの差を示す → 主催者が場所を選んだら
find_rooms → stage_arrangement。承認が要る場合はそこで止まる。
"""


def build_tools(container: Container) -> list:
    """Orchestrator の操作を ADK ツールとして公開する。"""
    orchestrator = container.orchestrator

    async def interpret_request(
        text: str, participant_uids: list[str], organizer_uid: str
    ) -> dict:
        """依頼文を解釈して会議の下書きを作る。

        Args:
            text: 「来週火曜14時、この6人で」のような依頼文。
            participant_uids: 参加者の uid 一覧。
            organizer_uid: 主催者の uid。
        Returns:
            meeting_id と解釈結果。
        """
        meeting, parsed = await orchestrator.interpret(
            text=text, participant_uids=participant_uids, organizer_uid=organizer_uid
        )
        return {
            "meeting_id": meeting.meeting_id,
            "starts_at": meeting.request.starts_at.isoformat(),
            "participant_count": len(meeting.request.participants),
            "parsed": parsed,
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
                    "breakdown": c.anonymized_legs(),
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

    async def find_rooms(meeting_id: str, station: str) -> dict:
        """候補地の周辺で条件に合う会議室を探す。

        Args:
            meeting_id: 会議ID。
            station: 候補地の駅名。
        Returns:
            会議室の一覧（料金・徒歩分・設備）。
        """
        rooms = await orchestrator.list_rooms(meeting_id, station)
        return {"rooms": [r.model_dump(mode="json") for r in rooms]}

    async def stage_arrangement(meeting_id: str, station: str, room_id: str) -> dict:
        """会議室を仮押さえし、有料なら主催者の承認を要求する。確定はしない。

        Args:
            meeting_id: 会議ID。
            station: 確定する候補地の駅名。
            room_id: 仮押さえする会議室ID。
        Returns:
            会議の状態と承認要否。
        """
        meeting = await orchestrator.stage_arrangement(
            meeting_id, station=station, room_id=room_id
        )
        return {
            "status": meeting.status.value,
            "approval": meeting.approval.model_dump(mode="json") if meeting.approval else None,
            "hold_id": meeting.hold.hold_id if meeting.hold else None,
        }

    async def check_service_disruptions(meeting_id: str) -> dict:
        """当日の運行障害を確認し、必要なら開始時刻調整を起案する。

        Args:
            meeting_id: 会議ID。
        Returns:
            起案の有無と内容。
        """
        _, proposal = await orchestrator.follow_up(meeting_id)
        return {
            "proposed": proposal is not None,
            "proposal": proposal.model_dump(mode="json") if proposal else None,
        }

    return [
        interpret_request,
        evaluate_candidates,
        compare_fairness_policies,
        find_rooms,
        stage_arrangement,
        check_service_disruptions,
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
            description="N人会議の場所を公平性ポリシー付きで最適化し手配するエージェント",
            instruction=INSTRUCTION,
            tools=build_tools(container),
        )
    except Exception:  # noqa: BLE001 — ADK 不在でもサービスは動かす
        logger.warning("ADK エージェントの構築に失敗しました", exc_info=True)
        return None
