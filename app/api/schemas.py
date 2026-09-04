"""API の入出力スキーマ。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import FairnessPolicy, Meeting


class ParticipantIn(BaseModel):
    """参加者1人分。受け取るのは名前と出発駅まで。"""

    name: str = ""
    origin_station: str


class MeetingCreateIn(BaseModel):
    """入力フォームの内容。"""

    starts_at: datetime | None = None
    participants: list[ParticipantIn] = Field(min_length=1)
    notes: str = Field(
        default="", description="その他の希望条件。公平性の基準をここから読み取る"
    )


class OptimizeIn(BaseModel):
    policy: FairnessPolicy | None = None


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
    breakdown: list[dict] = Field(description="1人ごとの内訳。負担の重い順に並ぶ")


class OptimizationOut(BaseModel):
    policy: FairnessPolicy
    policy_label: str
    explanation: str | None
    evaluated_at: datetime
    candidates: list[CandidateOut]
    places: dict[str, list[float]] = Field(
        default_factory=dict, description="駅名 -> [緯度, 経度]。地図の描画用"
    )


class MeetingOut(BaseModel):
    meeting_id: str
    status: str
    starts_at: datetime
    participant_count: int
    policy: FairnessPolicy | None
    optimization: OptimizationOut | None


def to_meeting_out(meeting: Meeting) -> MeetingOut:
    optimization = None
    names = {p.uid: p.display_name for p in meeting.request.participants}
    if meeting.optimization is not None:
        from app.domain.optimization import unfairness

        optimization = OptimizationOut(
            policy=meeting.optimization.policy,
            policy_label=meeting.optimization.policy.label,
            explanation=meeting.optimization.explanation,
            evaluated_at=meeting.optimization.evaluated_at,
            places=meeting.optimization.places,
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
                    breakdown=c.breakdown(names),
                )
                for c in meeting.optimization.candidates
            ],
        )

    return MeetingOut(
        meeting_id=meeting.meeting_id,
        status=meeting.status.value,
        starts_at=meeting.request.starts_at,
        participant_count=len(meeting.request.participants),
        policy=meeting.policy,
        optimization=optimization,
    )
