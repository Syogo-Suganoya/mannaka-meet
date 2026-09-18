"""永続化のポート。Firestore のコレクションに1対1で対応する。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import AuditLog, Meeting


class Repository(ABC):
    name: str = "repository"

    # meetings/{meetingId}
    @abstractmethod
    async def save_meeting(self, meeting: Meeting) -> Meeting: ...

    @abstractmethod
    async def get_meeting(self, meeting_id: str) -> Meeting | None: ...

    # audit/{logId}
    @abstractmethod
    async def append_audit(self, log: AuditLog) -> AuditLog: ...

    @abstractmethod
    async def list_audit(self, meeting_id: str | None = None) -> list[AuditLog]: ...
