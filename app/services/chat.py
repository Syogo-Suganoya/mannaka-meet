"""自作チャット。外部チャットサービスには依存しない。

会議の依頼はここから投げ、エージェントの返答もここに並ぶ。
全員が読む場なので、個人の出発駅・経路はここには出さない（設計書 §7-1）。
個人宛の情報は NotificationService でその人にだけ届ける。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from app.domain.models import TEAM_ROOM, ChatMessage
from app.repositories.base import Repository

AGENT_UID = "mannaka"
AGENT_NAME = "マンナカ"

# この語を含む発言をエージェントへの依頼とみなす
MENTION_PATTERN = re.compile(r"@(?:マンナカ|mannaka)", re.IGNORECASE)


def mentions_agent(text: str) -> bool:
    return bool(MENTION_PATTERN.search(text))


def strip_mention(text: str) -> str:
    return MENTION_PATTERN.sub("", text).strip()


class ChatService:
    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def post(
        self,
        *,
        text: str,
        author_uid: str,
        author_name: str,
        room: str = TEAM_ROOM,
        meeting_id: str | None = None,
        is_agent: bool = False,
    ) -> ChatMessage:
        message = ChatMessage(
            message_id=f"msg-{uuid.uuid4().hex[:12]}",
            room=room,
            meeting_id=meeting_id,
            author_uid=author_uid,
            author_name=author_name,
            text=text,
            is_agent=is_agent,
            created_at=datetime.now(timezone.utc),
        )
        return await self._repo.append_message(message)

    async def post_as_agent(
        self, *, text: str, room: str = TEAM_ROOM, meeting_id: str | None = None
    ) -> ChatMessage:
        return await self.post(
            text=text,
            author_uid=AGENT_UID,
            author_name=AGENT_NAME,
            room=room,
            meeting_id=meeting_id,
            is_agent=True,
        )

    async def history(self, room: str = TEAM_ROOM, *, limit: int = 100) -> list[ChatMessage]:
        return await self._repo.list_messages(room, limit=limit)
