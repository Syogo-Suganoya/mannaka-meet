"""REST API のテスト。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import Container, get_container
from app.main import app


@pytest.fixture
def client(container: Container):
    """テスト用コンテナを注入した TestClient。"""
    import app.api.routes as routes

    get_container.cache_clear()
    app.dependency_overrides[routes.container] = lambda: container
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    get_container.cache_clear()


def test_healthz_reports_providers(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert set(body["providers"]) == {
        "transit",
        "rooms",
        "calendar",
        "chat",
        "notifications",
        "llm",
        "repository",
    }


def test_full_flow_over_http(client, container: Container):
    assert client.post("/api/demo/seed").status_code == 200
    uids = [u["uid"] for u in client.get("/api/users").json()]
    assert len(uids) == 6

    created = client.post(
        "/api/meetings",
        json={
            "text": "来週火曜14時、この6人で。ホワイトボードのある部屋で",
            "participant_uids": uids,
            "organizer_uid": uids[0],
        },
    ).json()
    mid = created["meeting_id"]

    optimized = client.post(f"/api/meetings/{mid}/optimize", json={"policy": "minimax"}).json()
    assert optimized["policy"] == "minimax"
    assert optimized["optimization"]["policy_label"] == "最大負担の平準化"
    best = optimized["optimization"]["candidates"][0]
    assert best["rank"] == 1
    # 内訳は匿名化されている
    assert best["breakdown"][0]["participant"] == "参加者A"

    diff = client.get(f"/api/meetings/{mid}/policy-comparison").json()
    assert set(diff) == {"sum", "minimax", "cost"}

    station = best["station"]
    rooms = client.get(f"/api/meetings/{mid}/rooms", params={"station": station}).json()["rooms"]
    limit = container.settings.agent_spend_limit_yen
    paid = next(r for r in rooms if 0 < r["price_yen"] <= limit)

    staged = client.post(
        f"/api/meetings/{mid}/arrange", json={"station": station, "room_id": paid["room_id"]}
    ).json()
    assert staged["status"] == "awaiting_approval"

    # 承認前の確定は 428
    assert client.post(f"/api/meetings/{mid}/confirm", json={"station": station}).status_code == 428

    # 主催者以外の承認は 403
    forbidden = client.post(
        f"/api/meetings/{mid}/approval", json={"approved": True, "actor_uid": uids[1]}
    )
    assert forbidden.status_code == 403

    approved = client.post(
        f"/api/meetings/{mid}/approval", json={"approved": True, "actor_uid": uids[0]}
    ).json()
    assert approved["approval"]["status"] == "approved"

    confirmed = client.post(f"/api/meetings/{mid}/confirm", json={"station": station}).json()
    assert confirmed["status"] == "dispatched"
    assert len(confirmed["dispatches"]) == 6

    audit = client.get(f"/api/meetings/{mid}/audit").json()
    assert any(log["action"] == "approval.decided" for log in audit)

    # 経路は本人宛の通知にだけ届く
    for uid in uids:
        inbox = client.get("/api/notifications", params={"uid": uid}).json()
        assert sum(1 for n in inbox if n["kind"] == "route") == 1

    # 確定はチャットに流れる（集計値のみ）
    history = client.get("/api/chat/messages").json()
    decision_posts = [m for m in history if m["is_agent"] and "場所が確定" in m["text"]]
    assert len(decision_posts) == 1
    assert "採用した公平性基準" in decision_posts[0]["text"]


def test_over_limit_room_is_blocked_with_402(client, container: Container):
    """上限超過の予約は主催者が承認しようとしても通らない（設計書 §7-2）。"""
    client.post("/api/demo/seed")
    uids = [u["uid"] for u in client.get("/api/users").json()]
    mid = client.post(
        "/api/meetings",
        json={"text": "来週火曜14時、2時間", "participant_uids": uids, "organizer_uid": uids[0]},
    ).json()["meeting_id"]
    optimized = client.post(f"/api/meetings/{mid}/optimize", json={"policy": "sum"}).json()
    station = optimized["optimization"]["candidates"][0]["station"]

    rooms = client.get(f"/api/meetings/{mid}/rooms", params={"station": station}).json()["rooms"]
    limit = container.settings.agent_spend_limit_yen
    expensive = next((r for r in rooms if r["price_yen"] > limit), None)
    if expensive is None:
        pytest.skip("上限を超える会議室が在庫にない")

    client.post(
        f"/api/meetings/{mid}/arrange", json={"station": station, "room_id": expensive["room_id"]}
    )
    res = client.post(
        f"/api/meetings/{mid}/approval", json={"approved": True, "actor_uid": uids[0]}
    )
    assert res.status_code == 402
    assert "上限" in res.json()["detail"]


def test_chat_endpoint_starts_the_flow(client):
    client.post("/api/demo/seed")
    uids = [u["uid"] for u in client.get("/api/users").json()]

    res = client.post(
        "/api/chat/messages",
        json={
            "text": "@マンナカ 来週火曜14時、この6人で。いちばん遠い人の負担を減らしたい",
            "author_uid": uids[0],
        },
    ).json()
    assert res["meeting"]["policy"] == "minimax"
    assert res["message"]["is_agent"] is False

    history = client.get("/api/chat/messages").json()
    assert len(history) == 2  # 依頼とエージェントの返答
    assert history[1]["is_agent"] is True


def test_chat_without_mention_creates_no_meeting(client):
    client.post("/api/demo/seed")
    res = client.post(
        "/api/chat/messages", json={"text": "おつかれさまです", "author_uid": "tanaka"}
    ).json()
    assert res["meeting"] is None
    assert client.get("/api/meetings").json() == []


def test_notifications_can_be_marked_read(client):
    client.post("/api/demo/seed")
    uids = [u["uid"] for u in client.get("/api/users").json()]
    client.post(
        "/api/chat/messages",
        json={"text": "@マンナカ 来週火曜14時", "author_uid": uids[0]},
    )
    mid = client.get("/api/meetings").json()[0]["meeting_id"]
    station = (
        client.get(f"/api/meetings/{mid}").json()["optimization"]["candidates"][0]["station"]
    )
    rooms = client.get(f"/api/meetings/{mid}/rooms", params={"station": station}).json()["rooms"]
    free = next(r for r in rooms if r["price_yen"] == 0)
    client.post(f"/api/meetings/{mid}/arrange", json={"station": station, "room_id": free["room_id"]})
    client.post(f"/api/meetings/{mid}/confirm", json={"station": station})

    unread = client.get(
        "/api/notifications", params={"uid": uids[1], "unread_only": True}
    ).json()
    assert unread
    nid = unread[0]["notification_id"]
    assert client.post(f"/api/notifications/{nid}/read").json()["read"] is True
    assert not client.get(
        "/api/notifications", params={"uid": uids[1], "unread_only": True}
    ).json()


def test_unknown_meeting_returns_404(client):
    assert client.get("/api/meetings/nope").status_code == 404
    assert client.post("/api/meetings/nope/optimize", json={}).status_code == 404


def test_optimize_before_participants_returns_400(client):
    res = client.post(
        "/api/meetings",
        json={"text": "", "participant_uids": ["ghost"], "organizer_uid": "ghost"},
    )
    assert res.status_code == 400


def test_disruption_demo_endpoints(client):
    assert client.post("/api/demo/disruption", json={"line": "JR中央線"}).status_code == 200
    assert client.delete("/api/demo/disruption").status_code == 200
