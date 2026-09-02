"""REST API（Cloud Run: api 相当）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agents.arranger import ApprovalRequired, SpendLimitExceeded
from app.agents.orchestrator import InvalidTransition, MeetingNotFound
from app.api import schemas
from app.deps import Container, get_container
from app.domain.models import (
    TEAM_ROOM,
    ChatMessage,
    Notification,
    ServiceDisruption,
    UserProfile,
)

router = APIRouter()


def container() -> Container:
    return get_container()


# -- ユーザー ------------------------------------------------------------


@router.post("/users", response_model=UserProfile, tags=["users"])
async def register_user(body: schemas.UserIn, c: Container = Depends(container)):
    """参加者を登録する。保持するのは最寄り駅まで（設計書 §7-1）。"""
    return await c.orchestrator.register_user(UserProfile(**body.model_dump()))


@router.get("/users", response_model=list[UserProfile], tags=["users"])
async def list_users(c: Container = Depends(container)):
    return await c.repository.list_users()


# -- 会議 ----------------------------------------------------------------


@router.post("/meetings", response_model=schemas.MeetingOut, tags=["meetings"])
async def create_meeting(body: schemas.MeetingIn, c: Container = Depends(container)):
    """依頼文を解釈して会議の下書きを作る。"""
    try:
        meeting, _ = await c.orchestrator.interpret(
            text=body.text,
            participant_uids=body.participant_uids,
            organizer_uid=body.organizer_uid,
            starts_at=body.starts_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.to_meeting_out(meeting)


@router.get("/meetings", response_model=list[schemas.MeetingOut], tags=["meetings"])
async def list_meetings(c: Container = Depends(container)):
    return [schemas.to_meeting_out(m) for m in await c.repository.list_meetings()]


@router.get("/meetings/{meeting_id}", response_model=schemas.MeetingOut, tags=["meetings"])
async def get_meeting(meeting_id: str, c: Container = Depends(container)):
    meeting = await c.repository.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="会議が見つかりません")
    return schemas.to_meeting_out(meeting)


@router.post(
    "/meetings/{meeting_id}/optimize", response_model=schemas.MeetingOut, tags=["optimize"]
)
async def optimize(
    meeting_id: str, body: schemas.OptimizeIn, c: Container = Depends(container)
):
    """公平性ポリシーを適用して候補地を算出する。"""
    async with _errors():
        meeting = await c.orchestrator.optimize(meeting_id, body.policy)
    return schemas.to_meeting_out(meeting)


@router.get("/meetings/{meeting_id}/policy-comparison", tags=["optimize"])
async def policy_comparison(meeting_id: str, c: Container = Depends(container)):
    """3ポリシーそれぞれの結論の差を返す。"""
    async with _errors():
        return await c.orchestrator.policy_comparison(meeting_id)


@router.post("/meetings/{meeting_id}/post-candidates", tags=["notify"])
async def post_candidates(meeting_id: str, c: Container = Depends(container)):
    """候補をチャンネルに提示する。"""
    async with _errors():
        text = await c.orchestrator.post_candidates(meeting_id)
    return {"text": text}


# -- 手配 ----------------------------------------------------------------


@router.get("/meetings/{meeting_id}/rooms", response_model=schemas.RoomsOut, tags=["arrange"])
async def list_rooms(meeting_id: str, station: str, c: Container = Depends(container)):
    async with _errors():
        rooms = await c.orchestrator.list_rooms(meeting_id, station)
    return schemas.RoomsOut(station=station, rooms=rooms)


@router.post(
    "/meetings/{meeting_id}/arrange", response_model=schemas.MeetingOut, tags=["arrange"]
)
async def arrange(meeting_id: str, body: schemas.ArrangeIn, c: Container = Depends(container)):
    """会議室を仮押さえし、有料なら承認待ちにする。"""
    async with _errors():
        meeting = await c.orchestrator.stage_arrangement(
            meeting_id, station=body.station, room_id=body.room_id
        )
    return schemas.to_meeting_out(meeting)


@router.post(
    "/meetings/{meeting_id}/approval", response_model=schemas.MeetingOut, tags=["arrange"]
)
async def decide_approval(
    meeting_id: str, body: schemas.ApprovalIn, c: Container = Depends(container)
):
    """主催者による支出承認（設計書 §7-2）。"""
    async with _errors():
        meeting = await c.orchestrator.decide_approval(
            meeting_id, approved=body.approved, actor_uid=body.actor_uid, note=body.note
        )
    return schemas.to_meeting_out(meeting)


@router.post(
    "/meetings/{meeting_id}/confirm", response_model=schemas.MeetingOut, tags=["arrange"]
)
async def confirm(meeting_id: str, body: schemas.ConfirmIn, c: Container = Depends(container)):
    """確定 → カレンダー登録 → 個別経路の配信まで実行する。"""
    async with _errors():
        meeting = await c.orchestrator.confirm(meeting_id, station=body.station)
    return schemas.to_meeting_out(meeting)


# -- 当日フォロー ---------------------------------------------------------


@router.post(
    "/meetings/{meeting_id}/follow-up", response_model=schemas.FollowUpOut, tags=["follow"]
)
async def follow_up(meeting_id: str, c: Container = Depends(container)):
    """運行障害を確認し、必要なら開始時刻調整を起案する（確定はしない）。"""
    async with _errors():
        meeting, proposal = await c.orchestrator.follow_up(meeting_id)
    disruptions = proposal.disruptions if proposal else []
    return schemas.FollowUpOut(disruptions=disruptions, proposal=proposal)


@router.post(
    "/meetings/{meeting_id}/proposal", response_model=schemas.MeetingOut, tags=["follow"]
)
async def decide_proposal(
    meeting_id: str, body: schemas.ProposalDecisionIn, c: Container = Depends(container)
):
    async with _errors():
        meeting = await c.orchestrator.decide_proposal(
            meeting_id,
            proposal_id=body.proposal_id,
            accepted=body.accepted,
            actor_uid=body.actor_uid,
        )
    return schemas.to_meeting_out(meeting)


# -- 監査・可観測性 -------------------------------------------------------


@router.get("/meetings/{meeting_id}/audit", tags=["audit"])
async def meeting_audit(meeting_id: str, c: Container = Depends(container)):
    logs = await c.audit.list(meeting_id)
    return [log.model_dump(mode="json") for log in logs]


@router.get("/audit", tags=["audit"])
async def all_audit(c: Container = Depends(container)):
    logs = await c.audit.list()
    return [log.model_dump(mode="json") for log in logs]


# -- 自作チャット ---------------------------------------------------------


@router.get("/chat/messages", response_model=list[ChatMessage], tags=["chat"])
async def chat_history(room: str = TEAM_ROOM, c: Container = Depends(container)):
    return await c.chat.history(room)


@router.post("/chat/messages", response_model=schemas.ChatPostOut, tags=["chat"])
async def chat_post(body: schemas.ChatPostIn, c: Container = Depends(container)):
    """チャットに発言する。`@マンナカ` を含むと依頼として解釈し、候補算出まで進む。"""
    async with _errors():
        message, meeting = await c.orchestrator.handle_chat_message(
            text=body.text,
            author_uid=body.author_uid,
            participant_uids=body.participant_uids,
        )
    return schemas.ChatPostOut(
        message=message,
        meeting=schemas.to_meeting_out(meeting) if meeting else None,
    )


# -- アプリ内通知 ---------------------------------------------------------
# 通知はアプリ内のみ。メール・SMS・外部プッシュへは送らない。


@router.get("/notifications", response_model=list[Notification], tags=["notify"])
async def notifications(
    uid: str, unread_only: bool = False, c: Container = Depends(container)
):
    """本人宛の通知だけを返す（自分の経路・運賃、自分への承認依頼）。"""
    return await c.notifications.inbox(uid, unread_only=unread_only)


@router.post("/notifications/{notification_id}/read", tags=["notify"])
async def read_notification(notification_id: str, c: Container = Depends(container)):
    updated = await c.notifications.mark_read(notification_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="通知が見つかりません")
    return updated


# -- デモ操作（モック時のみ意味を持つ） -----------------------------------


@router.post("/demo/disruption", tags=["demo"])
async def inject_disruption(body: schemas.DisruptionIn, c: Container = Depends(container)):
    """モックの運行実況に遅延を注入する。"""
    if c.transit.name != "mock":
        raise HTTPException(status_code=409, detail="実APIモードでは遅延を注入できません")
    from app.adapters.mock.transit import MockTransitAdapter

    MockTransitAdapter.inject_disruption(
        ServiceDisruption(
            line=body.line,
            status=body.status,
            delay_minutes=body.delay_minutes,
            detail=body.detail,
        )
    )
    return {"injected": body.line, "delay_minutes": body.delay_minutes}


@router.delete("/demo/disruption", tags=["demo"])
async def clear_disruptions(c: Container = Depends(container)):
    if c.transit.name != "mock":
        raise HTTPException(status_code=409, detail="実APIモードでは操作できません")
    from app.adapters.mock.transit import MockTransitAdapter

    MockTransitAdapter.clear_disruptions()
    return {"cleared": True}


@router.post("/demo/seed", tags=["demo"])
async def seed(c: Container = Depends(container)):
    """デモ用の参加者6名（3拠点）を登録する。"""
    from app.demo import SEED_USERS

    for user in SEED_USERS:
        await c.orchestrator.register_user(user)
    return {"registered": [u.uid for u in SEED_USERS]}


# -- 例外の共通処理 -------------------------------------------------------


class _errors:
    """ドメイン例外を HTTP に写す小さなコンテキストマネージャ。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc is None:
            return False
        if isinstance(exc, MeetingNotFound):
            raise HTTPException(status_code=404, detail="会議が見つかりません") from exc
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, SpendLimitExceeded):
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        if isinstance(exc, ApprovalRequired):
            raise HTTPException(status_code=428, detail=str(exc)) from exc
        if isinstance(exc, (InvalidTransition, ValueError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return False
