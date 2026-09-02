"""アプリ内通知。

宛先1人に閉じた情報（自分の経路・運賃、自分への承認依頼）を届ける。
アプリ内に保存するだけで、メール・SMS・外部プッシュには一切送らない。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.models import Notification, NotificationKind
from app.repositories.base import Repository


class NotificationService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def notify(
        self,
        *,
        uid: str,
        kind: NotificationKind,
        title: str,
        body: str,
        meeting_id: str | None = None,
    ) -> Notification:
        notification = Notification(
            notification_id=f"ntf-{uuid.uuid4().hex[:12]}",
            uid=uid,
            meeting_id=meeting_id,
            kind=kind,
            title=title,
            body=body,
            created_at=datetime.now(timezone.utc),
        )
        return await self._repo.append_notification(notification)

    async def inbox(self, uid: str, *, unread_only: bool = False) -> list[Notification]:
        return await self._repo.list_notifications(uid, unread_only=unread_only)

    async def mark_read(self, notification_id: str) -> Notification | None:
        return await self._repo.mark_notification_read(notification_id)

    async def unread_count(self, uid: str) -> int:
        return len(await self._repo.list_notifications(uid, unread_only=True))
