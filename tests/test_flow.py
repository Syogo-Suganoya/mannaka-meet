"""依頼 → 候補算出のテスト。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.api.schemas import to_meeting_out
from app.deps import Container
from app.domain.models import FairnessPolicy, MeetingStatus
from tests.factories import SAMPLE_NOTES, SEED_PARTICIPANTS

WHEN = datetime(2026, 10, 6, 14, 0)


async def create(container: Container, notes: str = SAMPLE_NOTES):
    return await container.orchestrator.create_meeting(
        participants=SEED_PARTICIPANTS, starts_at=WHEN, notes=notes
    )


# -- 依頼 ----------------------------------------------------------------


async def test_create_meeting_uses_the_form_values(container: Container):
    meeting = await create(container)
    assert meeting.status is MeetingStatus.OPTIMIZED
    assert meeting.request.starts_at == WHEN
    assert [(p.display_name, p.origin_station) for p in meeting.request.participants] == (
        SEED_PARTICIPANTS
    )


async def test_notes_choose_the_fairness_policy(container: Container):
    meeting = await create(container, notes="いちばん遠い人の負担を減らしたい")
    assert meeting.policy is FairnessPolicy.MINIMAX


async def test_notes_can_ask_for_the_cheapest(container: Container):
    meeting = await create(container, notes="とにかく安く済ませたい")
    assert meeting.policy is FairnessPolicy.COST


async def test_nameless_participants_get_a_placeholder(container: Container):
    meeting = await container.orchestrator.create_meeting(
        participants=[("", "立川"), ("佐藤", "三鷹")], starts_at=WHEN
    )
    assert meeting.request.participants[0].display_name == "参加者1"


async def test_blank_station_is_rejected(container: Container):
    with pytest.raises(ValueError, match="出発駅が空です"):
        await container.orchestrator.create_meeting(
            participants=[("田中", "立川"), ("佐藤", "  ")], starts_at=WHEN
        )


async def test_unknown_station_is_rejected(container: Container):
    """未知の駅を黙って東京に寄せない。0分0円の参加者を作らないため。"""
    with pytest.raises(ValueError, match="経路を調べられない駅"):
        await container.orchestrator.create_meeting(
            participants=[("田中", "存在しない駅"), ("佐藤", "三鷹")], starts_at=WHEN
        )


async def test_no_participants_is_rejected(container: Container):
    with pytest.raises(ValueError):
        await container.orchestrator.create_meeting(participants=[], starts_at=WHEN)


# -- 最適化 --------------------------------------------------------------


async def test_optimize_ranks_candidates(container: Container):
    created = await create(container)
    meeting = await container.orchestrator.optimize(
        created.meeting_id, FairnessPolicy.MINIMAX
    )
    candidates = meeting.optimization.candidates
    assert len(candidates) == container.settings.candidate_limit
    assert [c.rank for c in candidates] == list(range(1, len(candidates) + 1))
    best = candidates[0]
    assert all(best.max_minutes <= c.max_minutes for c in candidates)
    assert all(len(c.legs) == 6 for c in candidates)


async def test_policy_changes_the_answer(container: Container):
    meeting = await create(container)
    results = {}
    for policy in FairnessPolicy:
        m = await container.orchestrator.optimize(meeting.meeting_id, policy)
        results[policy] = m.optimization.best
    assert results[FairnessPolicy.COST].total_fare_yen <= results[FairnessPolicy.SUM].total_fare_yen
    assert results[FairnessPolicy.MINIMAX].max_minutes <= results[FairnessPolicy.SUM].max_minutes


async def test_seed_participants_make_policies_disagree(container: Container):
    """この参加者構成では3ポリシーの結論が割れる。

    これが割れないと「どの基準で選んだかを宣言する」価値が伝わらないため、
    シードデータの性質としてテストで固定しておく。
    """
    meeting = await create(container)
    comparison = await container.orchestrator.policy_comparison(meeting.meeting_id)
    assert len({v["station"] for v in comparison.values()}) == 3


# -- 内訳（表情の元データ） ------------------------------------------------


async def test_breakdown_is_ordered_heaviest_first(container: Container):
    meeting = await create(container)
    rows = to_meeting_out(meeting).optimization.candidates[0].breakdown

    durations = [r["duration_minutes"] for r in rows]
    assert durations == sorted(durations, reverse=True)
    assert all(r["uid"] and r["participant"] and r["origin_station"] for r in rows)


async def test_every_participant_appears_in_every_candidate(container: Container):
    """表情は候補ごとに全員分そろっている必要がある。"""
    meeting = await create(container)
    uids = {p.uid for p in meeting.request.participants}
    for candidate in to_meeting_out(meeting).optimization.candidates:
        assert {r["uid"] for r in candidate.breakdown} == uids


async def test_places_are_real_coordinates(container: Container):
    """地図に打つ座標。相対値ではなく実際の緯度経度でないとタイルに載らない。"""
    meeting = await create(container)
    optimization = to_meeting_out(meeting).optimization
    stations = {p.origin_station for p in meeting.request.participants}
    stations |= {c.station for c in optimization.candidates}

    assert stations <= set(optimization.places)
    for lat, lng in optimization.places.values():
        assert 24 < lat < 46 and 122 < lng < 146  # 日本の範囲


# -- 監査 ----------------------------------------------------------------


async def test_audit_records_the_policy(container: Container):
    meeting = await create(container)
    logs = await container.audit.list(meeting.meeting_id)
    assert {"request.interpreted", "candidates.evaluated"} <= {log.action for log in logs}
    evaluated = next(log for log in logs if log.action == "candidates.evaluated")
    assert evaluated.payload["policy"] == meeting.policy.value
    assert "policy_comparison" in evaluated.payload
