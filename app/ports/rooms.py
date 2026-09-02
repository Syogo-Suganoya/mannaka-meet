"""会議室の空き照会・仮押さえ・確定のポート（スペイシー等の実APIに差し替え可能）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.models import MeetingRoom, RoomHold


class RoomsPort(ABC):
    name: str = "rooms"

    @abstractmethod
    async def search(
        self,
        *,
        station: str,
        starts_at: datetime,
        duration_minutes: int,
        headcount: int,
        equipment: list[str],
    ) -> list[MeetingRoom]:
        """条件に合う会議室を返す。"""

    @abstractmethod
    async def hold(
        self, *, room: MeetingRoom, starts_at: datetime, duration_minutes: int
    ) -> RoomHold:
        """仮押さえ。課金は確定時まで発生しない。"""

    @abstractmethod
    async def confirm(self, hold: RoomHold) -> RoomHold:
        """仮押さえを確定する。承認ゲートを通ったあとにのみ呼ばれる。"""

    @abstractmethod
    async def release(self, hold: RoomHold) -> None:
        """仮押さえを解放する。"""
