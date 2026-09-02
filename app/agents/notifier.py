"""配信エージェント（設計書 §4）。自律実行。

配信先は自作チャットとアプリ内通知の2つ。外部サービスへは送らない。

- チャット（全員が見る）: 集計値だけ。誰がどこから来るかは出さない
- 通知（本人だけが見る）: 自分の経路・出発時刻・運賃、自分への承認依頼

この2つを分けていることが、設計書 §7-1「自宅を晒さない」の実装そのものになる。
"""

from __future__ import annotations

from app.domain.models import (
    ApprovalRequest,
    CandidateArea,
    ChatMessage,
    Decision,
    DispatchItem,
    MeetingRequest,
    NotificationKind,
    OptimizationResult,
    RescheduleProposal,
    RouteLeg,
)
from app.services.audit import AuditService
from app.services.chat import ChatService
from app.services.notifications import NotificationService


class NotifierAgent:
    actor = "notifier-agent"

    def __init__(
        self,
        chat: ChatService,
        notifications: NotificationService,
        audit: AuditService,
    ) -> None:
        self._chat = chat
        self._notifications = notifications
        self._audit = audit

    # -- 候補提示（チャット） ---------------------------------------------

    async def post_candidates(
        self, *, meeting_id: str, result: OptimizationResult
    ) -> ChatMessage:
        lines = [
            f"【会議場所の候補】評価基準: {result.policy.label}（{result.policy.value}）",
        ]
        for c in result.candidates[:3]:
            n = len(c.legs) or 1
            lines.append(
                f"{c.rank}. {c.area}（{c.station}）— 合計{c.total_minutes}分 / "
                f"最長{c.max_minutes}分 / 平均{c.total_minutes // n}分 / "
                f"総額{c.total_fare_yen:,}円"
            )
        if result.explanation:
            lines.append("")
            lines.append(result.explanation)
        lines.append("")
        lines.append("※ 個人ごとの出発駅・経路は公開されません")

        message = await self._chat.post_as_agent(
            text="\n".join(lines), meeting_id=meeting_id
        )
        await self._audit.record(
            action="candidates.posted",
            actor=self.actor,
            meeting_id=meeting_id,
            channel="chat",
            policy=result.policy.value,
        )
        return message

    async def post_decision(
        self, *, meeting_id: str, decision: Decision, candidate: CandidateArea
    ) -> ChatMessage:
        n = len(candidate.legs) or 1
        room_line = (
            f"会議室: {decision.room.name}（{decision.room.price_yen:,}円 / "
            f"承認者 {decision.approver_uid}）"
            if decision.room
            else "会議室: 未手配"
        )
        text = (
            f"【場所が確定しました】\n"
            f"{decision.area}（{decision.station}）\n"
            f"{room_line}\n"
            f"採用した公平性基準: {decision.policy.label}（{decision.policy.value}）\n"
            f"合計移動時間 {candidate.total_minutes}分 / 最長 {candidate.max_minutes}分 / "
            f"平均 {candidate.total_minutes // n}分\n"
            f"各自の経路と出発時刻は個別の通知に届いています。"
        )
        message = await self._chat.post_as_agent(text=text, meeting_id=meeting_id)
        await self._audit.record(
            action="decision.posted", actor=self.actor, meeting_id=meeting_id, channel="chat"
        )
        return message

    async def post_proposal(self, *, meeting_id: str, text: str) -> ChatMessage:
        message = await self._chat.post_as_agent(text=text, meeting_id=meeting_id)
        await self._audit.record(
            action="proposal.posted", actor=self.actor, meeting_id=meeting_id, channel="chat"
        )
        return message

    async def post_text(self, *, meeting_id: str | None, text: str) -> ChatMessage:
        return await self._chat.post_as_agent(text=text, meeting_id=meeting_id)

    # -- 個別配信（アプリ内通知） -----------------------------------------

    async def dispatch_routes(
        self,
        *,
        meeting_id: str,
        request: MeetingRequest,
        decision: Decision,
        routes: dict[str, RouteLeg],
    ) -> list[DispatchItem]:
        items: list[DispatchItem] = []
        place = (
            f"{decision.room.name}（{decision.station}駅 徒歩{decision.room.walk_minutes}分）"
            if decision.room
            else f"{decision.area}（{decision.station}駅周辺）"
        )
        for participant in request.participants:
            leg = routes.get(participant.uid)
            if leg is None:
                continue
            depart = leg.depart_at.strftime("%H:%M") if leg.depart_at else "—"
            body = (
                f"会議場所が確定しました: {place}\n"
                f"開始 {request.starts_at.strftime('%m/%d %H:%M')}\n"
                f"あなたの出発目安: {leg.from_station} {depart} 発 "
                f"（所要{leg.duration_minutes}分 / 乗換{leg.transfers}回）\n"
                f"精算用運賃: 片道{leg.fare_yen:,}円\n"
                f"経路: {' → '.join(leg.lines) if leg.lines else '—'}"
            )
            await self._notifications.notify(
                uid=participant.uid,
                kind=NotificationKind.ROUTE,
                title=f"{place} へ {depart} 出発",
                body=body,
                meeting_id=meeting_id,
            )
            items.append(
                DispatchItem(
                    uid=participant.uid,
                    channel="in_app",
                    message=body,
                    depart_at=leg.depart_at,
                    fare_yen=leg.fare_yen,
                    delivered=True,
                )
            )

        await self._audit.record(
            action="routes.dispatched",
            actor=self.actor,
            meeting_id=meeting_id,
            channel="in_app",
            delivered=len(items),
            total=len(request.participants),
        )
        return items

    async def notify_approval_request(
        self, *, meeting_id: str, approval: ApprovalRequest
    ) -> None:
        """主催者にだけ承認依頼を通知する。"""
        over = approval.amount_yen > approval.spend_limit_yen
        body = (
            f"{approval.reason}\n"
            f"金額 {approval.amount_yen:,}円 / エージェントの支出上限 "
            f"{approval.spend_limit_yen:,}円\n"
            + ("上限を超えているため、このままでは確定できません。" if over else "承認すると確定できます。")
        )
        await self._notifications.notify(
            uid=approval.approver_uid,
            kind=NotificationKind.APPROVAL_REQUEST,
            title="会議室予約の承認が必要です",
            body=body,
            meeting_id=meeting_id,
        )

    async def notify_reschedule(
        self, *, meeting_id: str, proposal: RescheduleProposal, uids: list[str]
    ) -> None:
        """影響を受ける参加者に、時刻調整の起案を通知する。"""
        for uid in uids:
            await self._notifications.notify(
                uid=uid,
                kind=NotificationKind.RESCHEDULE,
                title=(
                    f"開始時刻の調整を検討中"
                    f"（{proposal.current_start.strftime('%H:%M')} → "
                    f"{proposal.proposed_start.strftime('%H:%M')}）"
                ),
                body=f"{proposal.rationale}\n※ 主催者が承諾するまで確定しません。",
                meeting_id=meeting_id,
            )
