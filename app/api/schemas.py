"""API の入出力スキーマ。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import (
    ApprovalRequest,
    ChatMessage,
    Decision,
    DispatchItem,
    FairnessPolicy,
    Meeting,
    MeetingRoom,
    RescheduleProposal,
    ServiceDisruption,
)


class UserIn(BaseModel):
    uid: str
    display_name: str
    origin_station: str = Field(description="最寄り駅のみ。住所は受け取らない")
    chat_id: str | None = None


class MeetingIn(BaseModel):
    text: str = Field(default="", description="「来週火曜14時、この6人で」等の依頼文")
    participant_uids: list[str]
    organizer_uid: str
    starts_at: datetime | None = None


class OptimizeIn(BaseModel):
    policy: FairnessPolicy | None = None


class ArrangeIn(BaseModel):
    station: str
    room_id: str | None = None


class ApprovalIn(BaseModel):
    approved: bool
    actor_uid: str
    note: str | None = None


class ConfirmIn(BaseModel):
    station: str | None = None


class ProposalDecisionIn(BaseModel):
    proposal_id: str
    accepted: bool
    actor_uid: str


class ChatPostIn(BaseModel):
    """自作チャットへの発言。`@マンナカ` を含めるとエージェントが動く。"""

    text: str
    author_uid: str
    participant_uids: list[str] | None = None


class ChatPostOut(BaseModel):
    message: ChatMessage
    meeting: "MeetingOut | None" = None


class DisruptionIn(BaseModel):
    """モックの運行実況に遅延を注入する（デモ用。実APIでは無効）。"""

    line: str
    delay_minutes: int = 12
    status: str = "遅延"
    detail: str = "人身事故の影響で遅れが出ています"


class CandidateOut(BaseModel):
    rank: int
    area: str
    station: str
    total_minutes: int
    max_minutes: int
    average_minutes: int
    total_fare_yen: int
    total_transfers: int
    unfairness_minutes: int
    score: float
    rationale: str | None
    breakdown: list[dict] = Field(description="匿名化した1人ごとの内訳")


class OptimizationOut(BaseModel):
    policy: FairnessPolicy
    policy_label: str
    explanation: str | None
    evaluated_at: datetime
    candidates: list[CandidateOut]


class MeetingOut(BaseModel):
    meeting_id: str
    status: str
    starts_at: datetime
    duration_minutes: int
    participant_count: int
    equipment: list[str]
    policy: FairnessPolicy | None
    optimization: OptimizationOut | None
    approval: ApprovalRequest | None
    decision: Decision | None
    dispatches: list[DispatchItem]
    proposals: list[RescheduleProposal]


class RoomsOut(BaseModel):
    station: str
    rooms: list[MeetingRoom]


class FollowUpOut(BaseModel):
    disruptions: list[ServiceDisruption]
    proposal: RescheduleProposal | None


ChatPostOut.model_rebuild()


def to_meeting_out(meeting: Meeting) -> MeetingOut:
    optimization = None
    if meeting.optimization is not None:
        from app.domain.optimization import unfairness

        optimization = OptimizationOut(
            policy=meeting.optimization.policy,
            policy_label=meeting.optimization.policy.label,
            explanation=meeting.optimization.explanation,
            evaluated_at=meeting.optimization.evaluated_at,
            candidates=[
                CandidateOut(
                    rank=c.rank,
                    area=c.area,
                    station=c.station,
                    total_minutes=c.total_minutes,
                    max_minutes=c.max_minutes,
                    average_minutes=c.total_minutes // (len(c.legs) or 1),
                    total_fare_yen=c.total_fare_yen,
                    total_transfers=c.total_transfers,
                    unfairness_minutes=unfairness(c),
                    score=c.score,
                    rationale=c.rationale,
                    breakdown=c.anonymized_legs(),
                )
                for c in meeting.optimization.candidates
            ],
        )

    return MeetingOut(
        meeting_id=meeting.meeting_id,
        status=meeting.status.value,
        starts_at=meeting.request.starts_at,
        duration_minutes=meeting.request.requirements.duration_minutes,
        participant_count=len(meeting.request.participants),
        equipment=meeting.request.requirements.equipment,
        policy=meeting.policy,
        optimization=optimization,
        approval=meeting.approval,
        decision=meeting.decision,
        dispatches=meeting.dispatches,
        proposals=meeting.proposals,
    )
