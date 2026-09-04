"""REST API のテスト。ログインは無く、参加者は毎回フォームから受け取る。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import Container, get_container
from app.main import app
from tests.factories import SEED_PARTICIPANTS


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


def create_meeting(client, participants=None, *, notes="") -> dict:
    res = client.post(
        "/api/meetings",
        json={
            "starts_at": "2026-10-06T14:00:00",
            "participants": [
                {"name": n, "origin_station": s}
                for n, s in (participants or SEED_PARTICIPANTS)
            ],
            "notes": notes,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_full_flow_over_http(client):
    meeting = create_meeting(client, notes="いちばん遠い人の負担を減らしたい")
    mid = meeting["meeting_id"]
    assert meeting["status"] == "optimized"
    assert meeting["policy"] == "minimax"
    assert meeting["participant_count"] == 6

    best = meeting["optimization"]["candidates"][0]
    assert {r["participant"] for r in best["breakdown"]} == {n for n, _ in SEED_PARTICIPANTS}
    assert best["breakdown"][0]["origin_station"]

    diff = client.get(f"/api/meetings/{mid}/policy-comparison").json()
    assert set(diff) == {"sum", "minimax", "cost"}

    # 基準を入れ替えると結論が変わる
    switched = client.post(f"/api/meetings/{mid}/optimize", json={"policy": "cost"}).json()
    assert switched["policy"] == "cost"
    assert switched["optimization"]["candidates"][0]["station"] != best["station"]

    actions = {log["action"] for log in client.get(f"/api/meetings/{mid}/audit").json()}
    assert {"request.interpreted", "candidates.evaluated"} <= actions


def test_meeting_survives_a_reload(client):
    mid = create_meeting(client)["meeting_id"]
    assert client.get(f"/api/meetings/{mid}").json()["meeting_id"] == mid


def test_unknown_station_returns_400(client):
    res = client.post(
        "/api/meetings",
        json={
            "starts_at": "2026-10-06T14:00:00",
            "participants": [{"name": "田中", "origin_station": "存在しない駅"}],
        },
    )
    assert res.status_code == 400
    assert "経路を調べられない駅" in res.json()["detail"]


def test_a_meeting_needs_at_least_one_participant(client):
    res = client.post("/api/meetings", json={"participants": []})
    assert res.status_code == 422


def test_unknown_meeting_returns_404(client):
    assert client.get("/api/meetings/nope").status_code == 404
    assert client.post("/api/meetings/nope/optimize", json={}).status_code == 404


def test_healthz_reports_providers(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert set(body["providers"]) == {"transit", "llm", "repository"}
