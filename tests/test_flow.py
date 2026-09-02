"""依頼→算出→手配→配信→当日フォローの一気通貫と、承認ゲートのテスト。"""

from __future__ import annotations

import pytest

from app.agents.arranger import ApprovalRequired, SpendLimitExceeded
from app.demo import SAMPLE_REQUEST, SEED_USERS
from app.domain.models import (
    FairnessPolicy,
    MeetingStatus,
    NotificationKind,
    ServiceDisruption,
)
from app.deps import Container
from app.settings import Settings

UIDS = [u.uid for u in SEED_USERS]
ORGANIZER = UIDS[0]


async def create(container: Container, text: str = SAMPLE_REQUEST):
    meeting, parsed = await container.orchestrator.interpret(
        text=text, participant_uids=UIDS, organizer_uid=ORGANIZER
    )
    return meeting, parsed


# -- 依頼 ----------------------------------------------------------------


async def test_interpret_builds_draft(seeded: Container):
    meeting, parsed = await create(seeded)
    assert meeting.status is MeetingStatus.DRAFT
    assert len(meeting.request.participants) == 6
    assert meeting.request.organizer.uid == ORGANIZER
    assert meeting.request.requirements.duration_minutes == 120
    assert "ホワイトボード" in meeting.request.requirements.equipment
    assert meeting.policy is FairnessPolicy.MINIMAX  # 依頼文から推定


async def test_interpret_rejects_unknown_participant(container: Container):
    with pytest.raises(ValueError):
        await container.orchestrator.interpret(
            text="", participant_uids=["nobody"], organizer_uid="nobody"
        )


# -- 最適化 --------------------------------------------------------------


async def test_optimize_ranks_candidates(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id, FairnessPolicy.MINIMAX)

    assert meeting.status is MeetingStatus.OPTIMIZED
    candidates = meeting.optimization.candidates
    assert len(candidates) == seeded.settings.candidate_limit
    assert [c.rank for c in candidates] == list(range(1, len(candidates) + 1))
    # minimax の最良候補は、最長移動時間が他候補以下
    best = candidates[0]
    assert all(best.max_minutes <= c.max_minutes for c in candidates)
    assert all(len(c.legs) == 6 for c in candidates)


async def test_policy_changes_the_answer(seeded: Container):
    meeting, _ = await create(seeded)
    results = {}
    for policy in FairnessPolicy:
        m = await seeded.orchestrator.optimize(meeting.meeting_id, policy)
        results[policy] = m.optimization.best
    assert results[FairnessPolicy.COST].total_fare_yen <= results[FairnessPolicy.SUM].total_fare_yen
    assert results[FairnessPolicy.MINIMAX].max_minutes <= results[FairnessPolicy.SUM].max_minutes


async def test_demo_seed_makes_policies_disagree(seeded: Container):
    """デモの参加者構成では3ポリシーの結論が割れる。

    これが割れないと「どの基準で選んだかを宣言する」価値が伝わらないため、
    シードデータの性質としてテストで固定しておく。
    """
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id)
    comparison = await seeded.orchestrator.policy_comparison(meeting.meeting_id)
    assert len({v["station"] for v in comparison.values()}) == 3


async def test_optimization_is_audited_with_policy(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id, FairnessPolicy.COST)
    logs = await seeded.audit.list(meeting.meeting_id)
    evaluated = [log for log in logs if log.action == "candidates.evaluated"]
    assert evaluated and evaluated[0].payload["policy"] == "cost"
    assert "policy_comparison" in evaluated[0].payload


# -- 自作チャット --------------------------------------------------------


async def test_chat_mention_starts_the_flow(seeded: Container):
    message, meeting = await seeded.orchestrator.handle_chat_message(
        text=f"@マンナカ {SAMPLE_REQUEST}", author_uid=ORGANIZER
    )
    assert message.author_uid == ORGANIZER
    assert meeting is not None
    assert meeting.status is MeetingStatus.OPTIMIZED
    assert meeting.request.organizer.uid == ORGANIZER

    history = await seeded.chat.history()
    assert history[0].message_id == message.message_id
    # エージェントの返答が続く
    assert history[-1].is_agent
    assert "評価基準" in history[-1].text


async def test_chat_without_mention_is_just_a_message(seeded: Container):
    message, meeting = await seeded.orchestrator.handle_chat_message(
        text="来週あたり集まりたいですね", author_uid=ORGANIZER
    )
    assert meeting is None
    assert [m.message_id for m in await seeded.chat.history()] == [message.message_id]


async def test_chat_message_strips_the_mention_before_parsing(seeded: Container):
    _, meeting = await seeded.orchestrator.handle_chat_message(
        text="@マンナカ 来週火曜14時、2時間で", author_uid=ORGANIZER
    )
    assert meeting.request.raw_text == "来週火曜14時、2時間で"
    assert meeting.request.requirements.duration_minutes == 120


# -- 承認ゲート ----------------------------------------------------------


async def test_paid_room_requires_approval(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id)
    station = meeting.optimization.best.station

    rooms = await seeded.orchestrator.list_rooms(meeting.meeting_id, station)
    paid = next(r for r in rooms if r.price_yen > 0)
    meeting = await seeded.orchestrator.stage_arrangement(
        meeting.meeting_id, station=station, room_id=paid.room_id
    )
    assert meeting.status is MeetingStatus.AWAITING_APPROVAL

    # 承認依頼は主催者にだけ届き、チャットには流れない
    organizer_inbox = await seeded.notifications.inbox(ORGANIZER)
    assert any(n.kind is NotificationKind.APPROVAL_REQUEST for n in organizer_inbox)
    for uid in UIDS[1:]:
        inbox = await seeded.notifications.inbox(uid)
        assert not any(n.kind is NotificationKind.APPROVAL_REQUEST for n in inbox)
    chat_text = " ".join(m.text for m in await seeded.chat.history())
    assert "承認" not in chat_text

    with pytest.raises(ApprovalRequired):
        await seeded.orchestrator.confirm(meeting.meeting_id, station=station)


async def test_free_room_is_auto_approved(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id)
    station = meeting.optimization.best.station

    rooms = await seeded.orchestrator.list_rooms(meeting.meeting_id, station)
    free = next((r for r in rooms if r.price_yen == 0), None)
    if free is None:
        pytest.skip("この候補地に無料の会議室がない")
    meeting = await seeded.orchestrator.stage_arrangement(
        meeting.meeting_id, station=station, room_id=free.room_id
    )
    assert meeting.approval.status == "auto_approved"
    assert meeting.status is MeetingStatus.ARRANGED


async def test_only_organizer_can_approve(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id)
    station = meeting.optimization.best.station
    rooms = await seeded.orchestrator.list_rooms(meeting.meeting_id, station)
    paid = next(r for r in rooms if r.price_yen > 0)
    await seeded.orchestrator.stage_arrangement(
        meeting.meeting_id, station=station, room_id=paid.room_id
    )
    with pytest.raises(PermissionError):
        await seeded.orchestrator.decide_approval(
            meeting.meeting_id, approved=True, actor_uid="sato"
        )


async def test_spend_limit_blocks_approval(settings: Settings):
    """上限額を下げると、主催者が承認しようとしても止まる（設計書 §7-2）。"""
    tight = settings.model_copy(update={"agent_spend_limit_yen": 100})
    container = Container(tight)
    for user in SEED_USERS:
        await container.orchestrator.register_user(user)

    meeting, _ = await create(container)
    meeting = await container.orchestrator.optimize(meeting.meeting_id)
    station = meeting.optimization.best.station
    rooms = await container.orchestrator.list_rooms(meeting.meeting_id, station)
    paid = next(r for r in rooms if r.price_yen > 0)
    await container.orchestrator.stage_arrangement(
        meeting.meeting_id, station=station, room_id=paid.room_id
    )
    with pytest.raises(SpendLimitExceeded):
        await container.orchestrator.decide_approval(
            meeting.meeting_id, approved=True, actor_uid=ORGANIZER
        )
    logs = await container.audit.list(meeting.meeting_id)
    assert any(log.action == "approval.rejected_by_limit" for log in logs)


async def test_rejection_releases_hold(seeded: Container):
    meeting, _ = await create(seeded)
    meeting = await seeded.orchestrator.optimize(meeting.meeting_id)
    station = meeting.optimization.best.station
    rooms = await seeded.orchestrator.list_rooms(meeting.meeting_id, station)
    paid = next(r for r in rooms if r.price_yen > 0)
    await seeded.orchestrator.stage_arrangement(
        meeting.meeting_id, station=station, room_id=paid.room_id
    )
    meeting = await seeded.orchestrator.decide_approval(
        meeting.meeting_id, approved=False, actor_uid=ORGANIZER
    )
    assert meeting.hold is None
    assert meeting.status is MeetingStatus.OPTIMIZED


# -- 確定・配信 ----------------------------------------------------------


async def test_end_to_end_dispatches_everyone(seeded: Container):
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    assert meeting.status is MeetingStatus.DISPATCHED
    assert meeting.decision is not None
    assert meeting.decision.calendar_event_id
    assert len(meeting.dispatches) == 6
    assert all(d.delivered for d in meeting.dispatches)

    # 経路は本人宛のアプリ内通知だけに届く
    for uid in UIDS:
        inbox = await seeded.notifications.inbox(uid)
        routes = [n for n in inbox if n.kind is NotificationKind.ROUTE]
        assert len(routes) == 1
        assert "精算用運賃" in routes[0].body


async def test_chat_post_does_not_leak_origins(seeded: Container):
    """チャットは全員が見る場なので、他人の出発駅が出てはいけない（設計書 §7-1）。"""
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    chat_text = " ".join(m.text for m in await seeded.chat.history())
    origins = {p.origin_station for p in meeting.request.participants}
    decided = meeting.decision.station
    for origin in origins - {decided}:
        assert origin not in chat_text
    # 個人名もチャットには出さない
    for participant in meeting.request.participants:
        assert participant.display_name not in chat_text


async def test_notifications_are_scoped_to_the_recipient(seeded: Container):
    """自分の通知に他人の出発駅・運賃が混ざらない。"""
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    by_uid = {p.uid: p for p in meeting.request.participants}
    for uid, participant in by_uid.items():
        route = next(
            n for n in await seeded.notifications.inbox(uid)
            if n.kind is NotificationKind.ROUTE
        )
        assert participant.origin_station in route.body
        others = {p.origin_station for u, p in by_uid.items() if u != uid}
        for origin in others - {participant.origin_station, meeting.decision.station}:
            assert origin not in route.body


async def test_end_to_end_stops_at_approval_without_auto_approve(seeded: Container):
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST, participant_uids=UIDS, organizer_uid=ORGANIZER
    )
    if meeting.status is MeetingStatus.AWAITING_APPROVAL:
        assert meeting.decision is None
    else:
        # 無料会議室が最安で選ばれた場合は承認不要のまま確定まで進む
        assert meeting.approval.status == "auto_approved"


# -- 当日フォロー --------------------------------------------------------


async def test_follow_up_without_disruption_proposes_nothing(seeded: Container):
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    _, proposal = await seeded.orchestrator.follow_up(meeting.meeting_id)
    assert proposal is None


async def test_follow_up_proposes_but_does_not_change_time(seeded: Container):
    from app.adapters.mock.transit import MockTransitAdapter

    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    original_start = meeting.request.starts_at
    line = next(iter(next(iter(meeting.routes.values())).lines))
    MockTransitAdapter.inject_disruption(
        ServiceDisruption(line=line, status="遅延", delay_minutes=18, detail="人身事故")
    )

    meeting, proposal = await seeded.orchestrator.follow_up(meeting.meeting_id)
    assert proposal is not None
    assert proposal.status == "proposed"
    assert proposal.delay_minutes == 20  # 5分刻みに切り上げ
    assert meeting.request.starts_at == original_start  # 起案だけでは変えない
    assert meeting.status is MeetingStatus.RESCHEDULE_PROPOSED


async def test_accepting_proposal_shifts_start_and_calendar(seeded: Container):
    from app.adapters.mock.transit import MockTransitAdapter

    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    original_start = meeting.request.starts_at
    line = next(iter(next(iter(meeting.routes.values())).lines))
    MockTransitAdapter.inject_disruption(
        ServiceDisruption(line=line, status="遅延", delay_minutes=18, detail="人身事故")
    )
    meeting, proposal = await seeded.orchestrator.follow_up(meeting.meeting_id)

    meeting = await seeded.orchestrator.decide_proposal(
        meeting.meeting_id,
        proposal_id=proposal.proposal_id,
        accepted=True,
        actor_uid=ORGANIZER,
    )
    assert meeting.request.starts_at > original_start
    event = seeded.calendar.events[meeting.decision.calendar_event_id]
    assert event["starts_at"] == meeting.request.starts_at.isoformat()
    assert meeting.proposals[0].status == "accepted"


async def test_declining_proposal_keeps_time(seeded: Container):
    from app.adapters.mock.transit import MockTransitAdapter

    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    original_start = meeting.request.starts_at
    line = next(iter(next(iter(meeting.routes.values())).lines))
    MockTransitAdapter.inject_disruption(
        ServiceDisruption(line=line, status="遅延", delay_minutes=18, detail="人身事故")
    )
    meeting, proposal = await seeded.orchestrator.follow_up(meeting.meeting_id)
    meeting = await seeded.orchestrator.decide_proposal(
        meeting.meeting_id,
        proposal_id=proposal.proposal_id,
        accepted=False,
        actor_uid=ORGANIZER,
    )
    assert meeting.request.starts_at == original_start
    assert meeting.proposals[0].status == "declined"


# -- 監査 ----------------------------------------------------------------


async def test_audit_trail_covers_the_whole_flow(seeded: Container):
    meeting = await seeded.orchestrator.run_end_to_end(
        text=SAMPLE_REQUEST,
        participant_uids=UIDS,
        organizer_uid=ORGANIZER,
        auto_approve=True,
    )
    actions = {log.action for log in await seeded.audit.list(meeting.meeting_id)}
    assert {
        "request.interpreted",
        "candidates.evaluated",
        "candidates.posted",
        "rooms.searched",
        "approval.requested",
        "meeting.arranged",
        "routes.dispatched",
    } <= actions
