from __future__ import annotations

import pytest

from app.deps import Container
from app.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="test",
        transit_provider="mock",
        repository="memory",
        llm_provider="stub",
    )


@pytest.fixture
def container(settings: Settings) -> Container:
    return Container(settings)
