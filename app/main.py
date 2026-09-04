"""マンナカ — N人会議場所最適化エージェント（Cloud Run エントリポイント）。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.deps import get_container

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mannaka")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = get_container()
    logger.info("mannaka 起動: providers=%s", container.providers())

    from app.agents.adk_agent import build_root_agent

    app.state.adk_agent = build_root_agent(container)
    if app.state.adk_agent is not None:
        logger.info("ADK エージェント有効: mannaka_orchestrator")
    yield


app = FastAPI(
    title="マンナカ",
    description="N人会議場所最適化エージェント。公平性ポリシーを宣言して場所を決め、手配・配信・当日フォローまで実行する。",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")


@app.get("/healthz", tags=["ops"])
async def healthz():
    """どのプロバイダ（モック / 実API）で動いているかを明示する。"""
    container = get_container()
    return {
        "status": "ok",
        "env": container.settings.env,
        "providers": container.providers(),
    }


class RevalidatingStatics(StaticFiles):
    """毎回 ETag で検証させる。

    Cache-Control が無いとブラウザが独自判断でキャッシュし、デプロイ後も
    古い CSS / JS を掴み続ける。中身が変わっていなければ 304 が返るだけなので
    転送量はほぼ増えない。
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if WEB_DIR.exists():
    app.mount("/static", RevalidatingStatics(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        """トップページ。機能と使い方の説明。"""
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app", include_in_schema=False)
    async def application():
        """アプリ本体。参加者を入れて候補地を見比べる。"""
        return FileResponse(WEB_DIR / "app.html")
