"""カレンダー登録のポート（Google Calendar API に差し替え可能）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class CalendarPort(ABC):
    name: str = "calendar"

    @abstractmethod
    async def create_event(
        self,
        *,
        title: str,
        starts_at: datetime,
        duration_minutes: int,
        location: str,
        attendee_uids: list[str],
        description: str = "",
    ) -> str:
        """イベントを作成し event_id を返す。"""

    @abstractmethod
    async def update_start(self, event_id: str, starts_at: datetime) -> None:
        """開始時刻を更新する（当日リスケの承諾後）。"""
