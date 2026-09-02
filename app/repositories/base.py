"""永続化のポート。設計書 §6 の Firestore コレクションに1対1で対応する。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import AuditLog, ChatMessage, Meeting, Notification, UserProfile


class Repository(ABC):
    name: str = "repository"

    # users/{uid}
    @abstractmethod
    async def save_user(self, user: UserProfile) -> UserProfile: ...

    @abstractmethod
    async def get_user(self, uid: str) -> UserProfile | None: ...

    @abstractmethod
    async def list_users(self) -> list[UserProfile]: ...

    # meetings/{meetingId}
    @abstractmethod
    async def save_meeting(self, meeting: Meeting) -> Meeting: ...

    @abstractmethod
    async def get_meeting(self, meeting_id: str) -> Meeting | None: ...

    @abstractmethod
    async def list_meetings(self) -> list[Meeting]: ...

    # audit/{logId}
    @abstractmethod
    async def append_audit(self, log: AuditLog) -> AuditLog: ...

    @abstractmethod
    async def list_audit(self, meeting_id: str | None = None) -> list[AuditLog]: ...

    # messages/{messageId} — 自作チャット
    @abstractmethod
    async def append_message(self, message: ChatMessage) -> ChatMessage: ...

    @abstractmethod
    async def list_messages(self, room: str, *, limit: int = 100) -> list[ChatMessage]: ...

    # notifications/{notificationId} — アプリ内通知
    @abstractmethod
    async def append_notification(self, notification: Notification) -> Notification: ...

    @abstractmethod
    async def list_notifications(
        self, uid: str, *, unread_only: bool = False
    ) -> list[Notification]: ...

    @abstractmethod
    async def mark_notification_read(self, notification_id: str) -> Notification | None: ...
