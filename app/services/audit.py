"""監査ログ。

Firestore（または memory）へ残すのと同時に、Cloud Logging が拾える
構造化JSONを stdout に出す。判断根拠は必ず payload に載せる。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.domain.models import AuditLog
from app.repositories.base import Repository

logger = logging.getLogger("mannaka.audit")


class AuditService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def record(
        self,
        *,
        action: str,
        actor: str,
        meeting_id: str | None = None,
        **payload,
    ) -> AuditLog:
        log = AuditLog(
            log_id=f"audit-{uuid.uuid4().hex[:12]}",
            meeting_id=meeting_id,
            actor=actor,
            action=action,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        await self._repo.append_audit(log)
        logger.info(
            json.dumps(
                {
                    "severity": "INFO",
                    "component": "mannaka.audit",
                    **log.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        )
        return log

    async def list(self, meeting_id: str | None = None) -> list[AuditLog]:
        return await self._repo.list_audit(meeting_id)
