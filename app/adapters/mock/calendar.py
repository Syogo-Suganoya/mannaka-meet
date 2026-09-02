"""Google Calendar API のモック。作成したイベントをプロセス内に保持する。"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.ports.calendar import CalendarPort


class MockCalendarAdapter(CalendarPort):
    name = "mock"

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}

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
        event_id = f"evt-{uuid.uuid4().hex[:10]}"
        self.events[event_id] = {
            "id": event_id,
            "title": title,
            "starts_at": starts_at.isoformat(),
            "duration_minutes": duration_minutes,
            "location": location,
            "attendees": attendee_uids,
            "description": description,
        }
        return event_id

    async def update_start(self, event_id: str, starts_at: datetime) -> None:
        if event_id in self.events:
            self.events[event_id]["starts_at"] = starts_at.isoformat()
