"""REST API（Cloud Run: api 相当）。

ログインは持たない。参加者は毎回フォームから受け取り、名前と出発駅以外は
サーバに残さない。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.agents.orchestrator import InvalidTransition, MeetingNotFound
from app.api import schemas
from app.deps import Container, get_container

router = APIRouter()


def container() -> Container:
    return get_container()


@router.post("/meetings", response_model=schemas.MeetingOut, tags=["meetings"])
async def create_meeting(
    body: schemas.MeetingCreateIn, c: Container = Depends(container)
):
    """フォームの内容から候補地を算出する。"""
    async with _errors():
        meeting = await c.orchestrator.create_meeting(
            participants=[(p.name, p.origin_station) for p in body.participants],
            starts_at=body.starts_at,
            notes=body.notes,
        )
    return schemas.to_meeting_out(meeting)


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
    """公平性ポリシーを入れ替えて候補地を算出し直す。"""
    async with _errors():
        meeting = await c.orchestrator.optimize(meeting_id, body.policy)
    return schemas.to_meeting_out(meeting)


@router.get("/meetings/{meeting_id}/policy-comparison", tags=["optimize"])
async def policy_comparison(meeting_id: str, c: Container = Depends(container)):
    """3ポリシーそれぞれの結論の差を返す。"""
    async with _errors():
        return await c.orchestrator.policy_comparison(meeting_id)


@router.get("/meetings/{meeting_id}/audit", tags=["audit"])
async def meeting_audit(meeting_id: str, c: Container = Depends(container)):
    logs = await c.audit.list(meeting_id)
    return [log.model_dump(mode="json") for log in logs]


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
        if isinstance(exc, (InvalidTransition, ValueError)):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return False
