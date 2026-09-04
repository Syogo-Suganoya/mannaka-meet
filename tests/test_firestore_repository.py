"""Firestore リポジトリの実接続テスト。

FIRESTORE_EMULATOR_HOST が設定されているときだけ動く（compose では既定で設定済み）。
インメモリ実装と同じ振る舞いをすることを、実際に読み書きして確かめる。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.domain.models import (
    AuditLog,
    FairnessPolicy,
    Meeting,
    MeetingRequest,
    MeetingStatus,
    Participant,
)
from tests.factories import SEED_PARTICIPANTS

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


async def test_meeting_roundtrip(repo):
    now = datetime.now(timezone.utc)
    meeting = Meeting(
        meeting_id=unique("mtg"),
        status=MeetingStatus.OPTIMIZED,
        request=MeetingRequest(
            starts_at=datetime(2026, 10, 6, 14, 0),
            participants=[
                Participant(uid=f"p{i + 1}", display_name=n, origin_station=s)
                for i, (n, s) in enumerate(SEED_PARTICIPANTS)
            ],
        ),
        policy=FairnessPolicy.MINIMAX,
        created_at=now,
        updated_at=now,
    )
    await repo.save_meeting(meeting)

    loaded = await repo.get_meeting(meeting.meeting_id)
    assert loaded.status is MeetingStatus.OPTIMIZED
    assert loaded.policy is FairnessPolicy.MINIMAX
    assert [p.origin_station for p in loaded.request.participants] == [
        s for _, s in SEED_PARTICIPANTS
    ]


async def test_missing_meeting_is_none(repo):
    assert await repo.get_meeting(unique("nope")) is None


async def test_audit_is_filtered_by_meeting(repo):
    meeting_id = unique("mtg")
    other = unique("mtg")
    now = datetime.now(timezone.utc)
    for target, action in [
        (meeting_id, "request.interpreted"),
        (meeting_id, "candidates.evaluated"),
        (other, "candidates.evaluated"),
    ]:
        await repo.append_audit(
            AuditLog(
                log_id=unique("audit"),
                meeting_id=target,
                actor="test",
                action=action,
                payload={"policy": "minimax"},
                created_at=now,
            )
        )

    logs = await repo.list_audit(meeting_id)
    assert {log.action for log in logs} == {"request.interpreted", "candidates.evaluated"}


async def test_full_flow_persists_through_firestore(repo, settings):
    """コンテナごと Firestore に差し替えて候補算出を流す。"""
    from app.deps import Container

    container = Container(settings, repository=repo)
    meeting = await container.orchestrator.create_meeting(
        participants=SEED_PARTICIPANTS,
        starts_at=datetime(2026, 10, 6, 14, 0),
        notes="いちばん遠い人の負担を減らしたい",
    )
    assert meeting.status is MeetingStatus.OPTIMIZED

    # 別インスタンスから読み直しても同じ状態が取れる
    from app.repositories.firestore import FirestoreRepository

    fresh = FirestoreRepository(os.environ.get("GOOGLE_CLOUD_PROJECT", "mannaka-local"))
    reloaded = await fresh.get_meeting(meeting.meeting_id)
    assert reloaded.optimization.best.station == meeting.optimization.best.station
    assert len(reloaded.optimization.candidates) == container.settings.candidate_limit
