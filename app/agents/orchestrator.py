"""Orchestrator（設計書 §6）。依頼の受付と候補算出の進行管理。

参加者と日時を受け取り、公平性の基準ごとに候補地を評価する。
どこにするかは決めない。決めるのは人。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.agents.optimizer import OptimizerAgent
from app.domain.models import (
    FairnessPolicy,
    Meeting,
    MeetingRequest,
    MeetingStatus,
    Participant,
)
from app.ports.llm import LlmPort
from app.repositories.base import Repository
from app.services.audit import AuditService

DEFAULT_POLICY = FairnessPolicy.MINIMAX


class MeetingNotFound(Exception):
    pass


class InvalidTransition(Exception):
    pass


class OrchestratorAgent:
    actor = "orchestrator-agent"

    def __init__(
        self,
        *,
        repository: Repository,
        audit: AuditService,
        llm: LlmPort,
        optimizer: OptimizerAgent,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._llm = llm
        self.optimizer = optimizer

    # -- 依頼の受付 -------------------------------------------------------

    async def create_meeting(
        self,
        *,
        participants: list[tuple[str, str]],
        starts_at: datetime | None = None,
        notes: str = "",
    ) -> Meeting:
        """入力フォームから会議を立て、候補算出まで進める。

        `participants` は (名前, 出発駅) の並び。日時はフォームで確定している。
        `notes`（その他の希望条件）からは公平性の基準を読み取る。
        """
        parsed = await self._llm.parse_request(notes) if notes else {}

        people = [
            Participant(
                uid=f"p{i + 1}",
                display_name=(name or "").strip() or f"参加者{i + 1}",
                origin_station=(station or "").strip(),
            )
            for i, (name, station) in enumerate(participants)
        ]
        if not people:
            raise ValueError("参加者が指定されていません")
        blank = [p.display_name for p in people if not p.origin_station]
        if blank:
            raise ValueError(f"出発駅が空です: {'、'.join(blank)}")

        unknown = await self.optimizer.unknown_stations([p.origin_station for p in people])
        if unknown:
            raise ValueError(
                f"経路を調べられない駅があります: {'、'.join(unknown)}。駅名を確認してください"
            )

        resolved_start = starts_at
        if resolved_start is None and "starts_at" in parsed:
            resolved_start = datetime.fromisoformat(parsed["starts_at"])
        if resolved_start is None:
            resolved_start = (datetime.now() + timedelta(days=1)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )

        now = datetime.now(timezone.utc)
        meeting = Meeting(
            meeting_id=f"mtg-{uuid.uuid4().hex[:10]}",
            status=MeetingStatus.DRAFT,
            request=MeetingRequest(
                starts_at=resolved_start,
                participants=people,
                raw_text=notes or None,
            ),
            policy=FairnessPolicy(parsed["policy"]) if parsed.get("policy") else None,
            created_at=now,
            updated_at=now,
        )
        await self._repo.save_meeting(meeting)
        await self._audit.record(
            action="request.interpreted",
            actor=self.actor,
            meeting_id=meeting.meeting_id,
            llm_provider=self._llm.name,
            raw_text=notes,
            parsed=parsed,
            resolved_start=resolved_start.isoformat(),
            participant_count=len(people),
        )
        return await self.optimize(meeting.meeting_id)

    # -- 最適化 -----------------------------------------------------------

    async def optimize(self, meeting_id: str, policy: FairnessPolicy | None = None) -> Meeting:
        meeting = await self._require(meeting_id)
        chosen = policy or meeting.policy or DEFAULT_POLICY
        meeting.optimization = await self.optimizer.evaluate(
            meeting_id=meeting_id, request=meeting.request, policy=chosen
        )
        meeting.policy = chosen
        meeting.status = MeetingStatus.OPTIMIZED
        return await self._touch(meeting)

    async def policy_comparison(self, meeting_id: str) -> dict:
        meeting = await self._require(meeting_id)
        if meeting.optimization is None:
            raise InvalidTransition("先に候補算出を実行してください")
        return await self.optimizer.compare_policies(meeting.optimization.candidates)

    # -- 内部 -------------------------------------------------------------

    async def _require(self, meeting_id: str) -> Meeting:
        meeting = await self._repo.get_meeting(meeting_id)
        if meeting is None:
            raise MeetingNotFound(meeting_id)
        return meeting

    async def _touch(self, meeting: Meeting) -> Meeting:
        meeting.updated_at = datetime.now(timezone.utc)
        return await self._repo.save_meeting(meeting)


__all__ = [
    "OrchestratorAgent",
    "MeetingNotFound",
    "InvalidTransition",
    "DEFAULT_POLICY",
]
