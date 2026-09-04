"""依存の組み立て（プロセス内シングルトン）。"""

from __future__ import annotations

from functools import lru_cache

from app.adapters import registry
from app.agents.optimizer import OptimizerAgent
from app.agents.orchestrator import OrchestratorAgent
from app.ports.llm import LlmPort
from app.ports.transit import TransitPort
from app.repositories.base import Repository
from app.services.audit import AuditService
from app.settings import Settings, get_settings


class Container:
    """アダプタとエージェントを1か所で束ねる。テストでは差し替えて使う。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transit: TransitPort | None = None,
        llm: LlmPort | None = None,
        repository: Repository | None = None,
    ) -> None:
        self.settings = settings
        self.transit = transit or registry.build_transit(settings)
        self.llm = llm or registry.build_llm(settings)
        self.repository = repository or registry.build_repository(settings)

        self.audit = AuditService(self.repository)
        self.optimizer = OptimizerAgent(
            self.transit,
            self.audit,
            self.llm,
            candidate_limit=settings.candidate_limit,
            buffer_minutes=settings.buffer_minutes,
        )
        self.orchestrator = OrchestratorAgent(
            repository=self.repository,
            audit=self.audit,
            llm=self.llm,
            optimizer=self.optimizer,
        )

    def providers(self) -> dict[str, str]:
        return {
            "transit": self.transit.name,
            "llm": self.llm.name,
            "repository": self.repository.name,
        }


@lru_cache
def get_container() -> Container:
    return Container(get_settings())
