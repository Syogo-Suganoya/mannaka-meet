"""Firestore リポジトリの実接続テスト。

FIRESTORE_EMULATOR_HOST が設定されているときだけ動く（compose では既定で設定済み）。
インメモリ実装と同じ振る舞いをすることを、実際に読み書きして確かめる。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models import (
    AuditLog,
    ChatMessage,
    FairnessPolicy,
    Meeting,
    MeetingRequest,
    MeetingRequirements,
    MeetingStatus,
    Notification,
    NotificationKind,
    Participant,
    UserProfile,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="Firestore エミュレータが無い環境ではスキップ",
)


@pytest.fixture
def repo():
    from app.repositories.firestore import FirestoreRepository

    return FirestoreRepository(os.environ.get("GOOGLE_CLOUD_PROJECT", "mannaka-local"))


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


async def test_user_roundtrip(repo):
    user = UserProfile(
        uid=unique("u"), display_name="テスト太郎", origin_station="立川", chat_id="U-test"
    )
    await repo.save_user(user)

    fetched = await repo.get_user(user.uid)
    assert fetched == user
    assert user.uid in {u.uid for u in await repo.list_users()}


async def test_missing_user_returns_none(repo):
    assert await repo.get_user(unique("missing")) is None


async def test_meeting_roundtrip_keeps_nested_models(repo):
    """会議は入れ子が深い。datetime と Enum が往復で壊れないことを確かめる。"""
    now = datetime.now(timezone.utc)
    starts_at = now + timedelta(days=7)
    meeting = Meeting(
        meeting_id=unique("mtg"),
        status=MeetingStatus.OPTIMIZED,
        request=MeetingRequest(
            starts_at=starts_at,
            participants=[
                Participant(
                    uid="a", display_name="A", origin_station="立川", is_organizer=True
                ),
                Participant(uid="b", display_name="B", origin_station="横浜"),
            ],
            requirements=MeetingRequirements(
                headcount=2, equipment=["ホワイトボード"], duration_minutes=120
            ),
            raw_text="来週火曜14時",
        ),
        policy=FairnessPolicy.MINIMAX,
        created_at=now,
        updated_at=now,
    )
    await repo.save_meeting(meeting)

    fetched = await repo.get_meeting(meeting.meeting_id)
    assert fetched is not None
    assert fetched.policy is FairnessPolicy.MINIMAX
    assert fetched.status is MeetingStatus.OPTIMIZED
    assert fetched.request.starts_at == starts_at
    assert fetched.request.organizer.uid == "a"
    assert fetched.request.requirements.equipment == ["ホワイトボード"]


async def test_audit_is_filtered_by_meeting(repo):
    meeting_id = unique("mtg")
    for action in ("candidates.evaluated", "approval.decided"):
        await repo.append_audit(
            AuditLog(
                log_id=unique("audit"),
                meeting_id=meeting_id,
                actor="tester",
                action=action,
                payload={"policy": "minimax"},
                created_at=datetime.now(timezone.utc),
            )
        )
    await repo.append_audit(
        AuditLog(
            log_id=unique("audit"),
            meeting_id=unique("other"),
            actor="tester",
            action="noise",
            payload={},
            created_at=datetime.now(timezone.utc),
        )
    )

    logs = await repo.list_audit(meeting_id)
    assert {log.action for log in logs} == {"candidates.evaluated", "approval.decided"}
    assert all(log.payload["policy"] == "minimax" for log in logs)


async def test_messages_are_scoped_to_room_and_ordered(repo):
    room = unique("room")
    base = datetime.now(timezone.utc)
    for i, text in enumerate(["古い発言", "新しい発言"]):
        await repo.append_message(
            ChatMessage(
                message_id=unique("msg"),
                room=room,
                author_uid="a",
                author_name="A",
                text=text,
                created_at=base + timedelta(seconds=i),
            )
        )
    await repo.append_message(
        ChatMessage(
            message_id=unique("msg"),
            room=unique("other"),
            author_uid="b",
            author_name="B",
            text="別の部屋",
            created_at=base,
        )
    )

    messages = await repo.list_messages(room)
    assert [m.text for m in messages] == ["古い発言", "新しい発言"]


async def test_notifications_are_scoped_and_readable(repo):
    uid = unique("u")
    other = unique("u")
    for target in (uid, other):
        await repo.append_notification(
            Notification(
                notification_id=unique("ntf"),
                uid=target,
                kind=NotificationKind.ROUTE,
                title="経路のお知らせ",
                body="立川 12:43 発",
                created_at=datetime.now(timezone.utc),
            )
        )

    inbox = await repo.list_notifications(uid)
    assert len(inbox) == 1
    assert await repo.list_notifications(uid, unread_only=True) == inbox

    updated = await repo.mark_notification_read(inbox[0].notification_id)
    assert updated.read is True
    assert await repo.list_notifications(uid, unread_only=True) == []
    assert len(await repo.list_notifications(uid)) == 1

    # 他人の通知は既読にならない
    assert all(not n.read for n in await repo.list_notifications(other))


async def test_mark_read_on_missing_notification_returns_none(repo):
    assert await repo.mark_notification_read(unique("missing")) is None


async def test_full_flow_persists_through_firestore(repo, settings):
    """コンテナごと Firestore に差し替えて一気通貫を流す。"""
    from app.demo import SAMPLE_REQUEST, SEED_USERS
    from app.deps import Container

    container = Container(settings.model_copy(update={"repository": "memory"}), repository=repo)
    for user in SEED_USERS:
        await container.orchestrator.register_user(user)

    meeting = await container.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=[u.uid for u in SEED_USERS],
        organizer_uid=SEED_USERS[0].uid,
        auto_approve=True,
    )
    assert meeting.status is MeetingStatus.DISPATCHED

    # 別インスタンスから読み直しても同じ状態が取れる
    from app.repositories.firestore import FirestoreRepository

    fresh = FirestoreRepository(os.environ.get("GOOGLE_CLOUD_PROJECT", "mannaka-local"))
    reloaded = await fresh.get_meeting(meeting.meeting_id)
    assert reloaded.status is MeetingStatus.DISPATCHED
    assert reloaded.decision.station == meeting.decision.station
    assert len(reloaded.routes) == 6
    assert len(await fresh.list_notifications(SEED_USERS[1].uid)) >= 1
