from __future__ import annotations

import pytest

from app.deps import Container
from app.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="test",
        transit_provider="mock",
        rooms_provider="mock",
        calendar_provider="mock",
        repository="memory",
        llm_provider="stub",
        agent_spend_limit_yen=5000,
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    from app.adapters.mock.transit import MockTransitAdapter

    MockTransitAdapter.clear_disruptions()
    return Container(settings)


@pytest.fixture
async def seeded(container: Container) -> Container:
    from app.demo import SEED_USERS

    for user in SEED_USERS:
        await container.orchestrator.register_user(user)
    return container
