"""Firestore 実装（REPOSITORY=firestore）。

ローカルでは FIRESTORE_EMULATOR_HOST を設定してエミュレータに接続する:
    docker compose --profile firestore up
    FIRESTORE_EMULATOR_HOST=firestore:8200
Pydantic モデルの入出力は JSON モードで往復させ、datetime を ISO 文字列に統一する。
"""

from __future__ import annotations

import asyncio

from app.domain.models import AuditLog, Meeting
from app.repositories.base import Repository


class FirestoreRepository(Repository):
    name = "firestore"

    def __init__(self, project: str) -> None:
        from google.cloud import firestore  # 遅延import
        from google.cloud.firestore_v1.base_query import FieldFilter

        self._db = firestore.Client(project=project)
        self._filter = FieldFilter

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
