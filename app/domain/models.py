"""マンナカのドメインモデル。

設計書 §6 のデータモデルに対応する。個人情報の扱いは §7-1 に従い、
参加者について保持するのは「最寄り駅」までとし住所は持たない。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 公平性ポリシー


class FairnessPolicy(str, Enum):
    """設計書 §4「最適化ロジック」の評価関数。"""

    SUM = "sum"  # 合計移動時間最小
    MINIMAX = "minimax"  # 最大負担の平準化
    COST = "cost"  # 総運賃最小

    @property
    def label(self) -> str:
        return {
            FairnessPolicy.SUM: "合計移動時間最小",
            FairnessPolicy.MINIMAX: "最大負担の平準化",
            FairnessPolicy.COST: "総運賃最小",
        }[self]


# ---------------------------------------------------------------- 参加者


class UserProfile(BaseModel):
    """users/{uid}.profile — 自宅住所は保持しない。"""

    uid: str
    display_name: str
    origin_station: str
    chat_id: str | None = None


class Participant(BaseModel):
    uid: str
    display_name: str
    origin_station: str
    chat_id: str | None = None
    is_organizer: bool = False


# ---------------------------------------------------------------- 依頼


class MeetingRequirements(BaseModel):
    headcount: int = Field(ge=1)
    equipment: list[str] = Field(default_factory=list)
    duration_minutes: int = 60


class MeetingRequest(BaseModel):
    """meetings/{id}.request — 日時・参加者・要件。"""

    starts_at: datetime
    participants: list[Participant]
    requirements: MeetingRequirements
    raw_text: str | None = None

    @property
    def organizer(self) -> Participant:
        for p in self.participants:
            if p.is_organizer:
                return p
        return self.participants[0]


# ---------------------------------------------------------------- 経路・候補


class RouteLeg(BaseModel):
    """1人分の経路サマリ。"""

    uid: str
    from_station: str
    to_station: str
    duration_minutes: int
    fare_yen: int
    transfers: int
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    lines: list[str] = Field(default_factory=list)


class CandidateArea(BaseModel):
    """1候補地の評価結果。内訳は RouteLeg の配列で持つ。"""

    area: str
    station: str
    legs: list[RouteLeg]
    total_minutes: int
    max_minutes: int
    total_fare_yen: int
    total_transfers: int
    score: float = 0.0
    rank: int = 0
    rationale: str | None = None

    def anonymized_legs(self) -> list[dict]:
        """他参加者に見せる用。誰がどこから来るかは伏せる（設計書 §7-1）。"""
        return [
            {
                "participant": f"参加者{chr(ord('A') + i)}",
                "duration_minutes": leg.duration_minutes,
                "fare_yen": leg.fare_yen,
                "transfers": leg.transfers,
            }
            for i, leg in enumerate(sorted(self.legs, key=lambda x: -x.duration_minutes))
        ]


class OptimizationResult(BaseModel):
    policy: FairnessPolicy
    candidates: list[CandidateArea]
    evaluated_at: datetime
    explanation: str | None = None

    @property
    def best(self) -> CandidateArea:
        return self.candidates[0]


# ---------------------------------------------------------------- 会議室・手配


class MeetingRoom(BaseModel):
    room_id: str
    provider: str
    name: str
    station: str
    walk_minutes: int
    capacity: int
    equipment: list[str] = Field(default_factory=list)
    price_yen: int = 0
    available: bool = True


class RoomHold(BaseModel):
    """仮押さえ。承認されるまで確定しない。"""

    hold_id: str
    room: MeetingRoom
    expires_at: datetime
    confirmed: bool = False


class ApprovalRequest(BaseModel):
    """有料予約の主催者承認ゲート（設計書 §7-2）。"""

    approval_id: str
    meeting_id: str
    requested_by: str = "arranger-agent"
    approver_uid: str
    amount_yen: int
    spend_limit_yen: int
    reason: str
    status: Literal["pending", "approved", "rejected", "auto_approved"] = "pending"
    decided_at: datetime | None = None
    note: str | None = None

    @property
    def within_limit(self) -> bool:
        return self.amount_yen <= self.spend_limit_yen


class Decision(BaseModel):
    """meetings/{id}.decision — 場所・会議室・承認者。"""

    area: str
    station: str
    room: MeetingRoom | None = None
    approver_uid: str | None = None
    policy: FairnessPolicy
    decided_at: datetime
    calendar_event_id: str | None = None


# ---------------------------------------------------------------- 配信・当日


class DispatchItem(BaseModel):
    uid: str
    channel: str
    message: str
    depart_at: datetime | None = None
    fare_yen: int = 0
    delivered: bool = False


# ---------------------------------------------------------------- チャット・通知
# 外部チャットサービスには依存せず、チャットも通知もアプリ内で完結させる。
# 通知はアプリ内のみ（メール・SMS・外部プッシュへは送らない）。

TEAM_ROOM = "team"


class ChatMessage(BaseModel):
    """自作チャットの1発言。依頼はここから投げる。

    チャットは全員が見る場なので、個人の出発駅や経路は流さない（設計書 §7-1）。
    個人宛の情報は Notification で本人にだけ届ける。
    """

    message_id: str
    room: str = TEAM_ROOM
    meeting_id: str | None = None
    author_uid: str
    author_name: str
    text: str
    is_agent: bool = False
    created_at: datetime


class NotificationKind(str, Enum):
    ROUTE = "route"  # 自分の経路・出発時刻・運賃
    APPROVAL_REQUEST = "approval_request"  # 主催者への支出承認依頼
    APPROVAL_RESULT = "approval_result"
    DECISION = "decision"  # 場所の確定
    RESCHEDULE = "reschedule"  # 開始時刻調整の起案・結果


class Notification(BaseModel):
    """アプリ内通知。宛先1人に閉じた情報を運ぶ。"""

    notification_id: str
    uid: str
    meeting_id: str | None = None
    kind: NotificationKind
    title: str
    body: str
    read: bool = False
    created_at: datetime


class ServiceDisruption(BaseModel):
    line: str
    status: str
    delay_minutes: int
    detail: str


class RescheduleProposal(BaseModel):
    """当日フォローは「起案」まで（設計書 §4 自律性）。"""

    proposal_id: str
    meeting_id: str
    disruptions: list[ServiceDisruption]
    affected_uids: list[str]
    current_start: datetime
    proposed_start: datetime
    delay_minutes: int
    rationale: str
    status: Literal["proposed", "accepted", "declined"] = "proposed"


# ---------------------------------------------------------------- 集約


class MeetingStatus(str, Enum):
    DRAFT = "draft"
    OPTIMIZED = "optimized"
    AWAITING_APPROVAL = "awaiting_approval"
    ARRANGED = "arranged"
    DISPATCHED = "dispatched"
    RESCHEDULE_PROPOSED = "reschedule_proposed"
    CANCELLED = "cancelled"


class Meeting(BaseModel):
    """meetings/{meetingId} の集約ルート。"""

    meeting_id: str
    status: MeetingStatus = MeetingStatus.DRAFT
    request: MeetingRequest
    policy: FairnessPolicy | None = None
    optimization: OptimizationResult | None = None
    approval: ApprovalRequest | None = None
    hold: RoomHold | None = None
    decision: Decision | None = None
    routes: dict[str, RouteLeg] = Field(default_factory=dict)
    dispatches: list[DispatchItem] = Field(default_factory=list)
    proposals: list[RescheduleProposal] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AuditLog(BaseModel):
    """audit/{logId} — ポリシー選択・支出承認・当日リスケの証跡（設計書 §7-4）。"""

    log_id: str
    meeting_id: str | None
    actor: str
    action: str
    payload: dict = Field(default_factory=dict)
    created_at: datetime
