"""手配エージェント（設計書 §4）。

会議室の空き照会・仮押さえまでは自律、有料予約の確定は主催者承認＋上限額の
二重ゲートを通す。承認履歴はすべて監査ログに残す（設計書 §7-2）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.models import (
    ApprovalRequest,
    CandidateArea,
    Decision,
    FairnessPolicy,
    MeetingRequest,
    MeetingRoom,
    RoomHold,
)
from app.ports.calendar import CalendarPort
from app.ports.rooms import RoomsPort
from app.services.audit import AuditService


class SpendLimitExceeded(Exception):
    """エージェントの支出上限を超える予約は自律実行しない。"""


class ApprovalRequired(Exception):
    """有料予約は主催者の承認なしに確定できない。"""


class ArrangerAgent:
    actor = "arranger-agent"

    def __init__(
        self,
        rooms: RoomsPort,
        calendar: CalendarPort,
        audit: AuditService,
        *,
        spend_limit_yen: int,
    ) -> None:
        self._rooms = rooms
        self._calendar = calendar
        self._audit = audit
        self.spend_limit_yen = spend_limit_yen

    # -- 空き照会・仮押さえ（自律） ---------------------------------------

    async def find_rooms(
        self, *, meeting_id: str, request: MeetingRequest, candidate: CandidateArea
    ) -> list[MeetingRoom]:
        rooms = await self._rooms.search(
            station=candidate.station,
            starts_at=request.starts_at,
            duration_minutes=request.requirements.duration_minutes,
            headcount=request.requirements.headcount,
            equipment=request.requirements.equipment,
        )
        await self._audit.record(
            action="rooms.searched",
            actor=self.actor,
            meeting_id=meeting_id,
            station=candidate.station,
            provider=self._rooms.name,
            found=len(rooms),
            requirements=request.requirements.model_dump(mode="json"),
        )
        return rooms

    async def hold_room(
        self, *, meeting_id: str, request: MeetingRequest, room: MeetingRoom
    ) -> RoomHold:
        hold = await self._rooms.hold(
            room=room,
            starts_at=request.starts_at,
            duration_minutes=request.requirements.duration_minutes,
        )
        await self._audit.record(
            action="room.held",
            actor=self.actor,
            meeting_id=meeting_id,
            hold_id=hold.hold_id,
            room=room.model_dump(mode="json"),
            note="仮押さえのみ。課金は確定時に発生する",
        )
        return hold

    async def release_hold(self, *, meeting_id: str, hold: RoomHold) -> None:
        await self._rooms.release(hold)
        await self._audit.record(
            action="room.released",
            actor=self.actor,
            meeting_id=meeting_id,
            hold_id=hold.hold_id,
        )

    # -- 承認ゲート -------------------------------------------------------

    async def request_approval(
        self, *, meeting_id: str, request: MeetingRequest, room: MeetingRoom
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id=f"apr-{uuid.uuid4().hex[:10]}",
            meeting_id=meeting_id,
            approver_uid=request.organizer.uid,
            amount_yen=room.price_yen,
            spend_limit_yen=self.spend_limit_yen,
            reason=f"{room.name}（{room.station}）の予約確定",
        )
        if room.price_yen == 0:
            # 無料（自社拠点等）は支出が発生しないため自動承認
            approval.status = "auto_approved"
            approval.decided_at = datetime.now(timezone.utc)
            approval.note = "無料会議室のため支出承認は不要"

        await self._audit.record(
            action="approval.requested",
            actor=self.actor,
            meeting_id=meeting_id,
            approval_id=approval.approval_id,
            approver_uid=approval.approver_uid,
            amount_yen=approval.amount_yen,
            spend_limit_yen=approval.spend_limit_yen,
            within_limit=approval.within_limit,
            status=approval.status,
        )
        return approval

    async def decide_approval(
        self,
        *,
        meeting_id: str,
        approval: ApprovalRequest,
        approved: bool,
        actor_uid: str,
        note: str | None = None,
    ) -> ApprovalRequest:
        if actor_uid != approval.approver_uid:
            raise PermissionError("承認できるのは主催者のみです")
        if approved and not approval.within_limit:
            await self._audit.record(
                action="approval.rejected_by_limit",
                actor=self.actor,
                meeting_id=meeting_id,
                approval_id=approval.approval_id,
                amount_yen=approval.amount_yen,
                spend_limit_yen=approval.spend_limit_yen,
            )
            raise SpendLimitExceeded(
                f"{approval.amount_yen:,}円は上限{approval.spend_limit_yen:,}円を超えます。"
                "上限額の見直しが必要です"
            )

        decided = approval.model_copy(
            update={
                "status": "approved" if approved else "rejected",
                "decided_at": datetime.now(timezone.utc),
                "note": note,
            }
        )
        await self._audit.record(
            action="approval.decided",
            actor=actor_uid,
            meeting_id=meeting_id,
            approval_id=decided.approval_id,
            status=decided.status,
            amount_yen=decided.amount_yen,
            note=note,
        )
        return decided

    # -- 確定 -------------------------------------------------------------

    async def confirm(
        self,
        *,
        meeting_id: str,
        request: MeetingRequest,
        candidate: CandidateArea,
        policy: FairnessPolicy,
        hold: RoomHold | None,
        approval: ApprovalRequest | None,
    ) -> tuple[Decision, RoomHold | None]:
        room = hold.room if hold else None
        if room is not None and room.price_yen > 0:
            if approval is None or approval.status not in ("approved", "auto_approved"):
                raise ApprovalRequired("有料会議室の確定には主催者の承認が必要です")

        confirmed_hold = await self._rooms.confirm(hold) if hold else None

        location = (
            f"{room.name}（{candidate.station}駅 徒歩{room.walk_minutes}分）"
            if room
            else f"{candidate.area}（{candidate.station}駅周辺）"
        )
        event_id = await self._calendar.create_event(
            title=f"会議（{candidate.area}）",
            starts_at=request.starts_at,
            duration_minutes=request.requirements.duration_minutes,
            location=location,
            attendee_uids=[p.uid for p in request.participants],
            description=(
                f"マンナカが{policy.label}（{policy.value}）で選定。"
                f"合計移動時間{candidate.total_minutes}分 / 最長{candidate.max_minutes}分。"
            ),
        )

        decision = Decision(
            area=candidate.area,
            station=candidate.station,
            room=confirmed_hold.room if confirmed_hold else None,
            approver_uid=approval.approver_uid if approval else None,
            policy=policy,
            decided_at=datetime.now(timezone.utc),
            calendar_event_id=event_id,
        )
        await self._audit.record(
            action="meeting.arranged",
            actor=self.actor,
            meeting_id=meeting_id,
            policy=policy.value,
            policy_label=policy.label,
            station=candidate.station,
            room_id=room.room_id if room else None,
            amount_yen=room.price_yen if room else 0,
            approver_uid=decision.approver_uid,
            calendar_event_id=event_id,
            calendar_provider=self._calendar.name,
        )
        return decision, confirmed_hold
