"""インメモリ実装。REPOSITORY=firestore で本物に差し替わる。"""

from __future__ import annotations

import asyncio

from app.domain.models import AuditLog, Meeting
from app.repositories.base import Repository


class InMemoryRepository(Repository):
    name = "memory"

    def __init__(self) -> None:
        self._meetings: dict[str, Meeting] = {}
        self._audit: list[AuditLog] = []
        self._lock = asyncio.Lock()

    async def save_meeting(self, meeting: Meeting) -> Meeting:
        async with self._lock:
            self._meetings[meeting.meeting_id] = meeting
        return meeting

    async def get_meeting(self, meeting_id: str) -> Meeting | None:
        return self._meetings.get(meeting_id)

    async def append_audit(self, log: AuditLog) -> AuditLog:
        async with self._lock:
            self._audit.append(log)
        return log

    async def list_audit(self, meeting_id: str | None = None) -> list[AuditLog]:
        if meeting_id is None:
            return list(self._audit)
        return [log for log in self._audit if log.meeting_id == meeting_id]
