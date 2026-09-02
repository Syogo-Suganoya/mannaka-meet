"""Google Calendar API アダプタ（CALENDAR_PROVIDER=google で有効）。

サービスアカウントの ADC を使う。Cloud Run 上ではメタデータサーバ経由で
資格情報が解決されるため、追加のキー配布は不要。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.ports.calendar import CalendarPort

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarAdapter(CalendarPort):
    name = "google"

    def __init__(self, calendar_id: str = "primary") -> None:
        from google.auth import default  # 遅延import
        from googleapiclient.discovery import build

        credentials, _ = default(scopes=SCOPES)
        self._service = build("calendar", "v3", credentials=credentials)
        self._calendar_id = calendar_id

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
        body = {
            "summary": title,
            "location": location,
            "description": description,
            "start": {"dateTime": starts_at.isoformat(), "timeZone": "Asia/Tokyo"},
            "end": {
                "dateTime": (starts_at + timedelta(minutes=duration_minutes)).isoformat(),
                "timeZone": "Asia/Tokyo",
            },
            "attendees": [{"email": uid} for uid in attendee_uids if "@" in uid],
        }
        event = await asyncio.to_thread(
            lambda: self._service.events()
            .insert(calendarId=self._calendar_id, body=body)
            .execute()
        )
        return event["id"]

    async def update_start(self, event_id: str, starts_at: datetime) -> None:
        def _patch() -> None:
            event = (
                self._service.events()
                .get(calendarId=self._calendar_id, eventId=event_id)
                .execute()
            )
            original_start = datetime.fromisoformat(event["start"]["dateTime"])
            original_end = datetime.fromisoformat(event["end"]["dateTime"])
            duration = original_end - original_start
            self._service.events().patch(
                calendarId=self._calendar_id,
                eventId=event_id,
                body={
                    "start": {"dateTime": starts_at.isoformat(), "timeZone": "Asia/Tokyo"},
                    "end": {
                        "dateTime": (starts_at + duration).isoformat(),
                        "timeZone": "Asia/Tokyo",
                    },
                },
            ).execute()

        await asyncio.to_thread(_patch)
