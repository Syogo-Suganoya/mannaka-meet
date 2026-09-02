"""会議室予約APIのモック（設計書 §8「MVPで捨てるもの」= 実会議室API連携）。

在庫はプロセス内に持つ。仮押さえ→確定→解放の状態遷移だけは本物同様に扱う。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from app.domain.models import MeetingRoom, RoomHold
from app.ports.rooms import RoomsPort

EQUIPMENT_POOL = [
    ["プロジェクタ", "ホワイトボード"],
    ["モニタ", "Web会議設備", "ホワイトボード"],
    ["ホワイトボード"],
    ["モニタ", "電源"],
]

ROOM_TEMPLATES = [
    ("会議室 {station}スクエア", 8, 3, 4400),
    ("{station}コワーキング Room A", 6, 5, 3200),
    ("{station}レンタルスペース 3F", 12, 7, 6600),
    ("{station}駅前ミーティングルーム", 4, 2, 2200),
    ("社内拠点 {station}オフィス", 10, 6, 0),  # 自社拠点は無料
]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


class MockRoomsAdapter(RoomsPort):
    name = "mock"

    def __init__(self) -> None:
        self._holds: dict[str, RoomHold] = {}

    async def search(
        self,
        *,
        station: str,
        starts_at: datetime,
        duration_minutes: int,
        headcount: int,
        equipment: list[str],
    ) -> list[MeetingRoom]:
        rooms: list[MeetingRoom] = []
        for i, (name_tpl, capacity, walk, price) in enumerate(ROOM_TEMPLATES):
            seed = _seed(station, name_tpl, starts_at.isoformat())
            equip = EQUIPMENT_POOL[(seed + i) % len(EQUIPMENT_POOL)]
            # 決定的な空き状況: 5室中1室は埋まっている
            available = (seed + i) % 5 != 0
            room = MeetingRoom(
                room_id=f"mock-{station}-{i}",
                provider="mock-rooms",
                name=name_tpl.format(station=station),
                station=station,
                walk_minutes=walk,
                capacity=capacity,
                equipment=equip,
                price_yen=int(price * max(1, duration_minutes) / 60),
                available=available,
            )
            rooms.append(room)

        wanted = set(equipment)
        matched = [
            r
            for r in rooms
            if r.available and r.capacity >= headcount and wanted.issubset(set(r.equipment))
        ]
        # 条件に合うものが無ければ設備条件を緩める（要件は提示時に明示する）
        if not matched:
            matched = [r for r in rooms if r.available and r.capacity >= headcount]
        return sorted(matched, key=lambda r: (r.price_yen, r.walk_minutes))

    async def hold(
        self, *, room: MeetingRoom, starts_at: datetime, duration_minutes: int
    ) -> RoomHold:
        hold = RoomHold(
            hold_id=f"hold-{room.room_id}-{int(starts_at.timestamp())}",
            room=room,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            confirmed=False,
        )
        self._holds[hold.hold_id] = hold
        return hold

    async def confirm(self, hold: RoomHold) -> RoomHold:
        confirmed = hold.model_copy(update={"confirmed": True})
        self._holds[hold.hold_id] = confirmed
        return confirmed

    async def release(self, hold: RoomHold) -> None:
        self._holds.pop(hold.hold_id, None)
