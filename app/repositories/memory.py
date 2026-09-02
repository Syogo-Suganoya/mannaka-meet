"""インメモリ実装（既定）。REPOSITORY=firestore で本物に差し替わる。"""

from __future__ import annotations

import asyncio

from app.domain.models import AuditLog, ChatMessage, Meeting, Notification, UserProfile
from app.repositories.base import Repository


class InMemoryRepository(Repository):
    name = "memory"

    def __init__(self) -> None:
        self._users: dict[str, UserProfile] = {}
        self._meetings: dict[str, Meeting] = {}
        self._audit: list[AuditLog] = []
        self._messages: list[ChatMessage] = []
        self._notifications: dict[str, Notification] = {}
        self._lock = asyncio.Lock()

    async def save_user(self, user: UserProfile) -> UserProfile:
        async with self._lock:
            self._users[user.uid] = user
        return user

    async def get_user(self, uid: str) -> UserProfile | None:
        return self._users.get(uid)

    async def list_users(self) -> list[UserProfile]:
        return list(self._users.values())

    async def save_meeting(self, meeting: Meeting) -> Meeting:
        async with self._lock:
            self._meetings[meeting.meeting_id] = meeting
        return meeting

    async def get_meeting(self, meeting_id: str) -> Meeting | None:
        return self._meetings.get(meeting_id)

    async def list_meetings(self) -> list[Meeting]:
        return sorted(self._meetings.values(), key=lambda m: m.created_at, reverse=True)

    async def append_audit(self, log: AuditLog) -> AuditLog:
        async with self._lock:
            self._audit.append(log)
        return log

    async def list_audit(self, meeting_id: str | None = None) -> list[AuditLog]:
        if meeting_id is None:
            return list(self._audit)
        return [log for log in self._audit if log.meeting_id == meeting_id]

    async def append_message(self, message: ChatMessage) -> ChatMessage:
        async with self._lock:
            self._messages.append(message)
        return message

    async def list_messages(self, room: str, *, limit: int = 100) -> list[ChatMessage]:
        messages = [m for m in self._messages if m.room == room]
        return messages[-limit:]

    async def append_notification(self, notification: Notification) -> Notification:
        async with self._lock:
            self._notifications[notification.notification_id] = notification
        return notification

    async def list_notifications(
        self, uid: str, *, unread_only: bool = False
    ) -> list[Notification]:
        items = [n for n in self._notifications.values() if n.uid == uid]
        if unread_only:
            items = [n for n in items if not n.read]
        return sorted(items, key=lambda n: n.created_at, reverse=True)

    async def mark_notification_read(self, notification_id: str) -> Notification | None:
        async with self._lock:
            current = self._notifications.get(notification_id)
            if current is None:
                return None
            updated = current.model_copy(update={"read": True})
            self._notifications[notification_id] = updated
        return updated
