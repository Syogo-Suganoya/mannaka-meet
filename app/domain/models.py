"""マンナカのドメインモデル。

設計書 §6 のデータモデルに対応する。個人情報の扱いは §7-1 に従い、
参加者について受け取るのは名前と出発駅までとし、住所も連絡先も持たない。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

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


class Participant(BaseModel):
    """1人分の入力。名前と出発駅だけ。住所も連絡先も持たない。"""

    uid: str  # このリクエスト内で一意な連番
    display_name: str
    origin_station: str


# ---------------------------------------------------------------- 依頼


class MeetingRequest(BaseModel):
    """meetings/{id}.request — 日時と参加者。"""

    starts_at: datetime
    participants: list[Participant]
    raw_text: str | None = None


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

    def breakdown(self, names: dict[str, str] | None = None) -> list[dict]:
        """1人ごとの内訳。負担の重い順に並べる。

        誰がどれだけ負担しているかを名前と出発駅で見せる。偏りを「参加者A」の
        まま示しても当人たちには意味が伝わらない、という判断による。
        名前が引けない場合だけ、通し記号にフォールバックする。
        """
        names = names or {}
        return [
            {
                "uid": leg.uid,
                "participant": names.get(leg.uid) or f"参加者{chr(ord('A') + i)}",
                "origin_station": leg.from_station,
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
    # 駅名 -> [緯度, 経度]。地図を描くためだけに持つ
    places: dict[str, list[float]] = Field(default_factory=dict)

    @property
    def best(self) -> CandidateArea:
        return self.candidates[0]


# ---------------------------------------------------------------- 集約


class MeetingStatus(str, Enum):
    DRAFT = "draft"
    OPTIMIZED = "optimized"


class Meeting(BaseModel):
    """meetings/{meetingId} の集約ルート。"""

    meeting_id: str
    status: MeetingStatus = MeetingStatus.DRAFT
    request: MeetingRequest
    policy: FairnessPolicy | None = None
    optimization: OptimizationResult | None = None
    created_at: datetime
    updated_at: datetime


class AuditLog(BaseModel):
    """audit/{logId} — どの基準でどう評価したかの証跡（設計書 §7-4）。"""

    log_id: str
    meeting_id: str | None
    actor: str
    action: str
    payload: dict = Field(default_factory=dict)
    created_at: datetime
