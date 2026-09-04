"""アダプタの組み立て。ここだけがモックと実APIの両方を知っている。

実API側の初期化に失敗した場合はモックに落として起動を継続し、
どのプロバイダで動いているかを /healthz と起動ログで明示する。
"""

from __future__ import annotations

import logging

from app.ports.llm import LlmPort
from app.ports.transit import TransitPort
from app.repositories.base import Repository
from app.settings import Settings

logger = logging.getLogger(__name__)


def build_transit(settings: Settings) -> TransitPort:
    if settings.transit_provider == "ekispert":
        try:
            from app.adapters.ekispert.transit import EkispertTransitAdapter

            return EkispertTransitAdapter(
                settings.ekispert_api_key, base_url=settings.ekispert_base_url
            )
        except Exception:  # noqa: BLE001
            logger.warning("駅すぱあとアダプタの初期化に失敗。モックで継続します", exc_info=True)

    from app.adapters.mock.transit import MockTransitAdapter

    return MockTransitAdapter()


def build_llm(settings: Settings) -> LlmPort:
    if settings.llm_provider == "gemini" and settings.google_api_key:
        try:
            from app.adapters.google.llm import GeminiLlmAdapter

            return GeminiLlmAdapter(settings.google_api_key, settings.gemini_model)
        except Exception:  # noqa: BLE001
            logger.warning("Gemini の初期化に失敗。ルールベースで継続します", exc_info=True)

    from app.adapters.mock.llm import StubLlmAdapter

    return StubLlmAdapter()


def build_repository(settings: Settings) -> Repository:
    if settings.repository == "firestore":
        try:
            from app.repositories.firestore import FirestoreRepository

            return FirestoreRepository(settings.google_cloud_project)
        except Exception:  # noqa: BLE001
            logger.warning("Firestore の初期化に失敗。インメモリで継続します", exc_info=True)

    from app.repositories.memory import InMemoryRepository

    return InMemoryRepository()
