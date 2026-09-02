"""Firestore 実装（REPOSITORY=firestore）。

ローカルでは FIRESTORE_EMULATOR_HOST を設定してエミュレータに接続する:
    docker compose --profile firestore up
    FIRESTORE_EMULATOR_HOST=firestore:8200
Pydantic モデルの入出力は JSON モードで往復させ、datetime を ISO 文字列に統一する。
"""

from __future__ import annotations

import asyncio

from app.domain.models import AuditLog, ChatMessage, Meeting, Notification, UserProfile
from app.repositories.base import Repository


class FirestoreRepository(Repository):
    name = "firestore"

    def __init__(self, project: str) -> None:
        from google.cloud import firestore  # 遅延import
        from google.cloud.firestore_v1.base_query import FieldFilter

        self._db = firestore.Client(project=project)
        self._filter = FieldFilter

    # -- users -----------------------------------------------------------

    async def save_user(self, user: UserProfile) -> UserProfile:
        await asyncio.to_thread(
            lambda: self._db.collection("users")
            .document(user.uid)
            .set({"profile": user.model_dump(mode="json")})
        )
        return user

    async def get_user(self, uid: str) -> UserProfile | None:
        doc = await asyncio.to_thread(
            lambda: self._db.collection("users").document(uid).get()
        )
        if not doc.exists:
            return None
        return UserProfile.model_validate(doc.to_dict()["profile"])

    async def list_users(self) -> list[UserProfile]:
        docs = await asyncio.to_thread(lambda: list(self._db.collection("users").stream()))
        return [UserProfile.model_validate(d.to_dict()["profile"]) for d in docs]

    # -- meetings --------------------------------------------------------

    async def save_meeting(self, meeting: Meeting) -> Meeting:
        await asyncio.to_thread(
            lambda: self._db.collection("meetings")
            .document(meeting.meeting_id)
            .set(meeting.model_dump(mode="json"))
        )
        return meeting

    async def get_meeting(self, meeting_id: str) -> Meeting | None:
        doc = await asyncio.to_thread(
            lambda: self._db.collection("meetings").document(meeting_id).get()
        )
        if not doc.exists:
            return None
        return Meeting.model_validate(doc.to_dict())

    async def list_meetings(self) -> list[Meeting]:
        docs = await asyncio.to_thread(
            lambda: list(self._db.collection("meetings").stream())
        )
        meetings = [Meeting.model_validate(d.to_dict()) for d in docs]
        return sorted(meetings, key=lambda m: m.created_at, reverse=True)

    # -- audit -----------------------------------------------------------

    async def append_audit(self, log: AuditLog) -> AuditLog:
        await asyncio.to_thread(
            lambda: self._db.collection("audit")
            .document(log.log_id)
            .set(log.model_dump(mode="json"))
        )
        return log

    async def list_audit(self, meeting_id: str | None = None) -> list[AuditLog]:
        def _query():
            col = self._db.collection("audit")
            if meeting_id is not None:
                return list(col.where(filter=self._filter("meeting_id", "==", meeting_id)).stream())
            return list(col.stream())

        docs = await asyncio.to_thread(_query)
        logs = [AuditLog.model_validate(d.to_dict()) for d in docs]
        return sorted(logs, key=lambda log: log.created_at)

    # -- messages（自作チャット） -----------------------------------------

    async def append_message(self, message: ChatMessage) -> ChatMessage:
        await asyncio.to_thread(
            lambda: self._db.collection("messages")
            .document(message.message_id)
            .set(message.model_dump(mode="json"))
        )
        return message

    async def list_messages(self, room: str, *, limit: int = 100) -> list[ChatMessage]:
        docs = await asyncio.to_thread(
            lambda: list(
                self._db.collection("messages")
                .where(filter=self._filter("room", "==", room))
                .stream()
            )
        )
        messages = [ChatMessage.model_validate(d.to_dict()) for d in docs]
        messages.sort(key=lambda m: m.created_at)
        return messages[-limit:]

    # -- notifications（アプリ内通知） ------------------------------------

    async def append_notification(self, notification: Notification) -> Notification:
        await asyncio.to_thread(
            lambda: self._db.collection("notifications")
            .document(notification.notification_id)
            .set(notification.model_dump(mode="json"))
        )
        return notification

    async def list_notifications(
        self, uid: str, *, unread_only: bool = False
    ) -> list[Notification]:
        # 絞り込みは uid だけにして、未読判定は Python 側で行う。
        # uid と read の複合クエリは本番 Firestore で複合インデックスを要求するため。
        docs = await asyncio.to_thread(
            lambda: list(
                self._db.collection("notifications")
                .where(filter=self._filter("uid", "==", uid))
                .stream()
            )
        )
        items = [Notification.model_validate(d.to_dict()) for d in docs]
        if unread_only:
            items = [n for n in items if not n.read]
        return sorted(items, key=lambda n: n.created_at, reverse=True)

    async def mark_notification_read(self, notification_id: str) -> Notification | None:
        ref = self._db.collection("notifications").document(notification_id)
        doc = await asyncio.to_thread(ref.get)
        if not doc.exists:
            return None
        await asyncio.to_thread(lambda: ref.update({"read": True}))
        return Notification.model_validate({**doc.to_dict(), "read": True})
