"""当日フォローエージェント（設計書 §4）。

運行障害を監視し、影響を受ける参加者を判定して開始時刻調整を「起案」する。
確定（カレンダー更新・通知）は主催者が承諾したときにだけ行う。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.domain.models import (
    Decision,
    MeetingRequest,
    RescheduleProposal,
    RouteLeg,
    ServiceDisruption,
)
from app.ports.calendar import CalendarPort
from app.ports.llm import LlmPort
from app.ports.transit import TransitPort
from app.services.audit import AuditService

# これ未満の遅延では起案しない（会議の頭出しでは吸収できる範囲）
MIN_PROPOSAL_DELAY_MINUTES = 5
# 提案時刻は5分刻みに丸める
ROUND_TO_MINUTES = 5


class FollowerAgent:
    actor = "follower-agent"

    def __init__(
        self,
        transit: TransitPort,
        calendar: CalendarPort,
        audit: AuditService,
        llm: LlmPort,
    ) -> None:
        self._transit = transit
        self._calendar = calendar
        self._audit = audit
        self._llm = llm

    async def monitor(
        self, *, meeting_id: str, routes: dict[str, RouteLeg]
    ) -> list[ServiceDisruption]:
        lines = sorted({line for leg in routes.values() for line in leg.lines})
        disruptions = await self._transit.disruptions(lines)
        await self._audit.record(
            action="service.monitored",
            actor=self.actor,
            meeting_id=meeting_id,
            transit_provider=self._transit.name,
            watched_lines=lines,
            disruptions=[d.model_dump(mode="json") for d in disruptions],
        )
        return disruptions

    async def propose(
        self,
        *,
        meeting_id: str,
        request: MeetingRequest,
        routes: dict[str, RouteLeg],
        disruptions: list[ServiceDisruption],
    ) -> RescheduleProposal | None:
        if not disruptions:
            return None

        affected: dict[str, int] = {}
        for uid, leg in routes.items():
            delay = sum(d.delay_minutes for d in disruptions if d.line in leg.lines)
            if delay > 0:
                affected[uid] = delay

        max_delay = max(affected.values(), default=0)
        if max_delay < MIN_PROPOSAL_DELAY_MINUTES:
            await self._audit.record(
                action="reschedule.skipped",
                actor=self.actor,
                meeting_id=meeting_id,
                max_delay_minutes=max_delay,
                threshold_minutes=MIN_PROPOSAL_DELAY_MINUTES,
                reason="遅延が閾値未満のため起案しない",
            )
            return None

        rounded = -(-max_delay // ROUND_TO_MINUTES) * ROUND_TO_MINUTES
        proposed_start = request.starts_at + timedelta(minutes=rounded)

        affected_names = [
            p.display_name for p in request.participants if p.uid in affected
        ]
        fallback = (
            f"{'、'.join(d.line for d in disruptions)}の遅延（最大{max_delay}分）により、"
            f"{len(affected)}名の到着が遅れる見込みです。"
            f"開始を{request.starts_at.strftime('%H:%M')}→"
            f"{proposed_start.strftime('%H:%M')}へ{rounded}分後ろ倒しすることを提案します。"
        )
        prompt = (
            "会議の開始時刻調整を提案する、簡潔で丁寧な日本語の文面を2文で書いてください。"
            "個人名は出さず、人数と時刻のみ触れてください。\n"
            f"遅延路線: {', '.join(f'{d.line}({d.delay_minutes}分)' for d in disruptions)}\n"
            f"影響人数: {len(affected)}名\n"
            f"現在の開始: {request.starts_at.strftime('%H:%M')} / "
            f"提案する開始: {proposed_start.strftime('%H:%M')}"
        )
        rationale = await self._llm.explain(prompt, fallback=fallback)

        proposal = RescheduleProposal(
            proposal_id=f"prop-{uuid.uuid4().hex[:10]}",
            meeting_id=meeting_id,
            disruptions=disruptions,
            affected_uids=list(affected.keys()),
            current_start=request.starts_at,
            proposed_start=proposed_start,
            delay_minutes=rounded,
            rationale=rationale,
        )
        await self._audit.record(
            action="reschedule.proposed",
            actor=self.actor,
            meeting_id=meeting_id,
            proposal_id=proposal.proposal_id,
            delay_minutes=rounded,
            affected_count=len(affected),
            affected_names=affected_names,
            disruptions=[d.model_dump(mode="json") for d in disruptions],
            current_start=request.starts_at.isoformat(),
            proposed_start=proposed_start.isoformat(),
            note="起案のみ。確定には主催者の承諾が必要",
        )
        return proposal

    async def accept(
        self,
        *,
        meeting_id: str,
        proposal: RescheduleProposal,
        decision: Decision,
        actor_uid: str,
    ) -> RescheduleProposal:
        if decision.calendar_event_id:
            await self._calendar.update_start(
                decision.calendar_event_id, proposal.proposed_start
            )
        accepted = proposal.model_copy(update={"status": "accepted"})
        await self._audit.record(
            action="reschedule.accepted",
            actor=actor_uid,
            meeting_id=meeting_id,
            proposal_id=proposal.proposal_id,
            new_start=proposal.proposed_start.isoformat(),
            calendar_event_id=decision.calendar_event_id,
        )
        return accepted

    async def decline(
        self, *, meeting_id: str, proposal: RescheduleProposal, actor_uid: str
    ) -> RescheduleProposal:
        declined = proposal.model_copy(update={"status": "declined"})
        await self._audit.record(
            action="reschedule.declined",
            actor=actor_uid,
            meeting_id=meeting_id,
            proposal_id=proposal.proposal_id,
        )
        return declined

    @staticmethod
    def now() -> datetime:
        return datetime.now()
