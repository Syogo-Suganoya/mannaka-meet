"""Orchestrator（設計書 §4）。チャット依頼の解釈と進行管理。

依頼→算出→手配→配信→当日フォローの一気通貫を、承認ゲートを挟みながら進める。
各サブエージェントは自分の担当だけを知り、状態遷移はここが持つ。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.agents.arranger import ApprovalRequired, ArrangerAgent
from app.agents.follower import FollowerAgent
from app.agents.notifier import NotifierAgent
from app.agents.optimizer import OptimizerAgent
from app.domain.models import (
    CandidateArea,
    ChatMessage,
    FairnessPolicy,
    Meeting,
    MeetingRequest,
    MeetingRequirements,
    MeetingStatus,
    Participant,
    RescheduleProposal,
    UserProfile,
)
from app.ports.llm import LlmPort
from app.repositories.base import Repository
from app.services.audit import AuditService
from app.services.chat import ChatService, mentions_agent, strip_mention

DEFAULT_POLICY = FairnessPolicy.MINIMAX


class MeetingNotFound(Exception):
    pass


class InvalidTransition(Exception):
    pass


class OrchestratorAgent:
    actor = "orchestrator-agent"

    def __init__(
        self,
        *,
        repository: Repository,
        audit: AuditService,
        llm: LlmPort,
        chat: ChatService,
        optimizer: OptimizerAgent,
        arranger: ArrangerAgent,
        notifier: NotifierAgent,
        follower: FollowerAgent,
    ) -> None:
        self._repo = repository
        self._audit = audit
        self._llm = llm
        self.chat = chat
        self.optimizer = optimizer
        self.arranger = arranger
        self.notifier = notifier
        self.follower = follower

    # -- 依頼の受付 -------------------------------------------------------

    async def interpret(
        self,
        *,
        text: str,
        participant_uids: list[str],
        organizer_uid: str,
        starts_at: datetime | None = None,
    ) -> tuple[Meeting, dict]:
        """「来週火曜14時、この6人で」を構造化し、下書きの会議を作る。"""
        parsed = await self._llm.parse_request(text) if text else {}

        participants: list[Participant] = []
        for uid in participant_uids:
            profile = await self._repo.get_user(uid)
            if profile is None:
                raise ValueError(f"未登録の参加者です: {uid}")
            participants.append(
                Participant(
                    uid=profile.uid,
                    display_name=profile.display_name,
                    origin_station=profile.origin_station,
                    chat_id=profile.chat_id,
                    is_organizer=(profile.uid == organizer_uid),
                )
            )
        if not participants:
            raise ValueError("参加者が指定されていません")
        if not any(p.is_organizer for p in participants):
            participants[0].is_organizer = True

        resolved_start = starts_at
        if resolved_start is None and "starts_at" in parsed:
            resolved_start = datetime.fromisoformat(parsed["starts_at"])
        if resolved_start is None:
            # 日時が読み取れない場合は翌営業日14:00を仮置きし、UIで修正させる
            resolved_start = (datetime.now() + timedelta(days=1)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )

        request = MeetingRequest(
            starts_at=resolved_start,
            participants=participants,
            requirements=MeetingRequirements(
                headcount=max(int(parsed.get("headcount", len(participants))), len(participants)),
                equipment=list(parsed.get("equipment", [])),
                duration_minutes=int(parsed.get("duration_minutes", 60)),
            ),
            raw_text=text or None,
        )

        now = datetime.now(timezone.utc)
        meeting = Meeting(
            meeting_id=f"mtg-{uuid.uuid4().hex[:10]}",
            status=MeetingStatus.DRAFT,
            request=request,
            policy=FairnessPolicy(parsed["policy"]) if parsed.get("policy") else None,
            created_at=now,
            updated_at=now,
        )
        await self._repo.save_meeting(meeting)
        await self._audit.record(
            action="request.interpreted",
            actor=self.actor,
            meeting_id=meeting.meeting_id,
            llm_provider=self._llm.name,
            raw_text=text,
            parsed=parsed,
            resolved_start=resolved_start.isoformat(),
            participant_count=len(participants),
        )
        return meeting, parsed

    async def handle_chat_message(
        self, *, text: str, author_uid: str, participant_uids: list[str] | None = None
    ) -> tuple[ChatMessage, Meeting | None]:
        """自作チャットへの発言を受け取る。依頼なら候補算出まで進める。

        エージェントへのメンションが無ければ、ただの発言として記録するだけ。
        """
        author = await self._repo.get_user(author_uid)
        author_name = author.display_name if author else author_uid
        message = await self.chat.post(
            text=text, author_uid=author_uid, author_name=author_name
        )

        if not mentions_agent(text):
            return message, None

        uids = participant_uids or [u.uid for u in await self._repo.list_users()]
        meeting, _ = await self.interpret(
            text=strip_mention(text), participant_uids=uids, organizer_uid=author_uid
        )
        meeting = await self.optimize(meeting.meeting_id)
        await self.post_candidates(meeting.meeting_id)
        return message, meeting

    # -- 最適化 -----------------------------------------------------------

    async def optimize(self, meeting_id: str, policy: FairnessPolicy | None = None) -> Meeting:
        meeting = await self._require(meeting_id)
        chosen = policy or meeting.policy or DEFAULT_POLICY
        result = await self.optimizer.evaluate(
            meeting_id=meeting_id, request=meeting.request, policy=chosen
        )
        meeting.policy = chosen
        meeting.optimization = result
        meeting.status = MeetingStatus.OPTIMIZED
        # 承認前に手配を進めていた場合はやり直しになるため状態を落とす
        meeting.approval = None
        meeting.hold = None
        meeting.decision = None
        return await self._touch(meeting)

    async def policy_comparison(self, meeting_id: str) -> dict:
        meeting = await self._require(meeting_id)
        if meeting.optimization is None:
            raise InvalidTransition("先に候補算出を実行してください")
        return await self.optimizer.compare_policies(meeting.optimization.candidates)

    # -- 手配 -------------------------------------------------------------

    async def list_rooms(self, meeting_id: str, station: str) -> list:
        meeting = await self._require(meeting_id)
        candidate = self._candidate(meeting, station)
        return await self.arranger.find_rooms(
            meeting_id=meeting_id, request=meeting.request, candidate=candidate
        )

    async def stage_arrangement(
        self, meeting_id: str, *, station: str, room_id: str | None
    ) -> Meeting:
        """会議室を仮押さえし、必要なら承認を要求する（確定はしない）。"""
        meeting = await self._require(meeting_id)
        candidate = self._candidate(meeting, station)

        if room_id is None:
            meeting.hold = None
            meeting.approval = None
            meeting.status = MeetingStatus.OPTIMIZED
            return await self._touch(meeting)

        rooms = await self.arranger.find_rooms(
            meeting_id=meeting_id, request=meeting.request, candidate=candidate
        )
        room = next((r for r in rooms if r.room_id == room_id), None)
        if room is None:
            raise ValueError(f"選択された会議室が見つかりません: {room_id}")

        meeting.hold = await self.arranger.hold_room(
            meeting_id=meeting_id, request=meeting.request, room=room
        )
        meeting.approval = await self.arranger.request_approval(
            meeting_id=meeting_id, request=meeting.request, room=room
        )
        meeting.status = (
            MeetingStatus.ARRANGED
            if meeting.approval.status == "auto_approved"
            else MeetingStatus.AWAITING_APPROVAL
        )
        if meeting.status is MeetingStatus.AWAITING_APPROVAL:
            # 承認依頼は主催者だけに届ける（チャットには流さない）
            await self.notifier.notify_approval_request(
                meeting_id=meeting_id, approval=meeting.approval
            )
        return await self._touch(meeting)

    async def decide_approval(
        self, meeting_id: str, *, approved: bool, actor_uid: str, note: str | None = None
    ) -> Meeting:
        meeting = await self._require(meeting_id)
        if meeting.approval is None:
            raise InvalidTransition("承認待ちの申請がありません")

        meeting.approval = await self.arranger.decide_approval(
            meeting_id=meeting_id,
            approval=meeting.approval,
            approved=approved,
            actor_uid=actor_uid,
            note=note,
        )
        if not approved:
            if meeting.hold is not None:
                await self.arranger.release_hold(meeting_id=meeting_id, hold=meeting.hold)
                meeting.hold = None
            meeting.status = MeetingStatus.OPTIMIZED
        return await self._touch(meeting)

    async def confirm(self, meeting_id: str, *, station: str | None = None) -> Meeting:
        """確定 → カレンダー登録 → 個別配信までを実行する。"""
        meeting = await self._require(meeting_id)
        if meeting.optimization is None:
            raise InvalidTransition("先に候補算出を実行してください")

        target = station or (
            meeting.hold.room.station if meeting.hold else meeting.optimization.best.station
        )
        candidate = self._candidate(meeting, target)

        decision, hold = await self.arranger.confirm(
            meeting_id=meeting_id,
            request=meeting.request,
            candidate=candidate,
            policy=meeting.policy or DEFAULT_POLICY,
            hold=meeting.hold,
            approval=meeting.approval,
        )
        meeting.decision = decision
        meeting.hold = hold
        meeting.routes = {leg.uid: leg for leg in candidate.legs}
        meeting.status = MeetingStatus.ARRANGED
        await self._touch(meeting)

        await self.notifier.post_decision(
            meeting_id=meeting_id, decision=decision, candidate=candidate
        )
        meeting.dispatches = await self.notifier.dispatch_routes(
            meeting_id=meeting_id,
            request=meeting.request,
            decision=decision,
            routes=meeting.routes,
        )
        meeting.status = MeetingStatus.DISPATCHED
        return await self._touch(meeting)

    async def post_candidates(self, meeting_id: str) -> str:
        meeting = await self._require(meeting_id)
        if meeting.optimization is None:
            raise InvalidTransition("先に候補算出を実行してください")
        message = await self.notifier.post_candidates(
            meeting_id=meeting_id, result=meeting.optimization
        )
        return message.text

    # -- 当日フォロー -----------------------------------------------------

    async def follow_up(self, meeting_id: str) -> tuple[Meeting, RescheduleProposal | None]:
        meeting = await self._require(meeting_id)
        if not meeting.routes:
            raise InvalidTransition("確定済みの経路がありません")

        disruptions = await self.follower.monitor(
            meeting_id=meeting_id, routes=meeting.routes
        )
        proposal = await self.follower.propose(
            meeting_id=meeting_id,
            request=meeting.request,
            routes=meeting.routes,
            disruptions=disruptions,
        )
        if proposal is not None:
            meeting.proposals.append(proposal)
            meeting.status = MeetingStatus.RESCHEDULE_PROPOSED
            await self.notifier.post_proposal(
                meeting_id=meeting_id,
                text=(
                    f"【開始時刻の調整を提案します】\n{proposal.rationale}\n"
                    f"（承諾されるまでカレンダーは変更しません）"
                ),
            )
            await self.notifier.notify_reschedule(
                meeting_id=meeting_id, proposal=proposal, uids=proposal.affected_uids
            )
        return await self._touch(meeting), proposal

    async def decide_proposal(
        self, meeting_id: str, *, proposal_id: str, accepted: bool, actor_uid: str
    ) -> Meeting:
        meeting = await self._require(meeting_id)
        if meeting.decision is None:
            raise InvalidTransition("確定済みの会議ではありません")
        index = next(
            (i for i, p in enumerate(meeting.proposals) if p.proposal_id == proposal_id),
            None,
        )
        if index is None:
            raise ValueError(f"提案が見つかりません: {proposal_id}")

        proposal = meeting.proposals[index]
        if accepted:
            meeting.proposals[index] = await self.follower.accept(
                meeting_id=meeting_id,
                proposal=proposal,
                decision=meeting.decision,
                actor_uid=actor_uid,
            )
            meeting.request.starts_at = proposal.proposed_start
            for leg in meeting.routes.values():
                if leg.depart_at is not None:
                    leg.depart_at += timedelta(minutes=proposal.delay_minutes)
                if leg.arrive_at is not None:
                    leg.arrive_at += timedelta(minutes=proposal.delay_minutes)
            await self.notifier.dispatch_routes(
                meeting_id=meeting_id,
                request=meeting.request,
                decision=meeting.decision,
                routes=meeting.routes,
            )
        else:
            meeting.proposals[index] = await self.follower.decline(
                meeting_id=meeting_id, proposal=proposal, actor_uid=actor_uid
            )
        meeting.status = MeetingStatus.DISPATCHED
        return await self._touch(meeting)

    # -- 一気通貫（デモ用） ------------------------------------------------

    async def run_end_to_end(
        self,
        *,
        text: str,
        participant_uids: list[str],
        organizer_uid: str,
        policy: FairnessPolicy | None = None,
        auto_approve: bool = False,
    ) -> Meeting:
        """依頼→算出→提示→（承認）→確定→配信を通しで実行する。

        有料会議室が選ばれ auto_approve=False の場合は承認待ちで止まる。
        """
        meeting, _ = await self.interpret(
            text=text, participant_uids=participant_uids, organizer_uid=organizer_uid
        )
        meeting = await self.optimize(meeting.meeting_id, policy)
        await self.post_candidates(meeting.meeting_id)

        best = meeting.optimization.best
        rooms = await self.list_rooms(meeting.meeting_id, best.station)
        # エージェントは自分の支出上限内で収まる部屋を選ぶ。上限内が無ければ
        # 最安を挙げて、上限超過であることを主催者に突きつける。
        affordable = [r for r in rooms if r.price_yen <= self.arranger.spend_limit_yen]
        room_id = (affordable or rooms)[0].room_id if rooms else None
        meeting = await self.stage_arrangement(
            meeting.meeting_id, station=best.station, room_id=room_id
        )

        if meeting.status is MeetingStatus.AWAITING_APPROVAL:
            if not auto_approve:
                return meeting
            meeting = await self.decide_approval(
                meeting.meeting_id,
                approved=True,
                actor_uid=meeting.request.organizer.uid,
                note="デモ実行のため主催者が即時承認",
            )
        return await self.confirm(meeting.meeting_id, station=best.station)

    # -- ユーザー ---------------------------------------------------------

    async def register_user(self, user: UserProfile) -> UserProfile:
        saved = await self._repo.save_user(user)
        await self._audit.record(
            action="user.registered",
            actor=self.actor,
            uid=user.uid,
            origin_station=user.origin_station,
            note="保持するのは最寄り駅のみ。住所は保存しない",
        )
        return saved

    # -- 内部 -------------------------------------------------------------

    async def _require(self, meeting_id: str) -> Meeting:
        meeting = await self._repo.get_meeting(meeting_id)
        if meeting is None:
            raise MeetingNotFound(meeting_id)
        return meeting

    async def _touch(self, meeting: Meeting) -> Meeting:
        meeting.updated_at = datetime.now(timezone.utc)
        return await self._repo.save_meeting(meeting)

    @staticmethod
    def _candidate(meeting: Meeting, station: str) -> CandidateArea:
        if meeting.optimization is None:
            raise InvalidTransition("先に候補算出を実行してください")
        for c in meeting.optimization.candidates:
            if c.station == station:
                return c
        raise ValueError(f"候補にない場所です: {station}")


__all__ = [
    "OrchestratorAgent",
    "MeetingNotFound",
    "InvalidTransition",
    "ApprovalRequired",
    "DEFAULT_POLICY",
]
