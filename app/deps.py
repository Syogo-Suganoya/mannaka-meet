"""依存の組み立て（プロセス内シングルトン）。"""

from __future__ import annotations

from functools import lru_cache

from app.adapters import registry
from app.agents.arranger import ArrangerAgent
from app.agents.follower import FollowerAgent
from app.agents.notifier import NotifierAgent
from app.agents.optimizer import OptimizerAgent
from app.agents.orchestrator import OrchestratorAgent
from app.ports.calendar import CalendarPort
from app.ports.llm import LlmPort
from app.ports.rooms import RoomsPort
from app.ports.transit import TransitPort
from app.repositories.base import Repository
from app.services.audit import AuditService
from app.services.chat import ChatService
from app.services.notifications import NotificationService
from app.settings import Settings, get_settings


class Container:
    """アダプタとエージェントを1か所で束ねる。テストでは差し替えて使う。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transit: TransitPort | None = None,
        rooms: RoomsPort | None = None,
        calendar: CalendarPort | None = None,
        llm: LlmPort | None = None,
        repository: Repository | None = None,
    ) -> None:
        self.settings = settings
        self.transit = transit or registry.build_transit(settings)
        self.rooms = rooms or registry.build_rooms(settings)
        self.calendar = calendar or registry.build_calendar(settings)
        self.llm = llm or registry.build_llm(settings)
        self.repository = repository or registry.build_repository(settings)

        # チャットと通知は自作。外部サービスには送らない（通知はアプリ内のみ）
        self.audit = AuditService(self.repository)
        self.chat = ChatService(self.repository)
        self.notifications = NotificationService(self.repository)
        self.optimizer = OptimizerAgent(
            self.transit,
            self.audit,
            self.llm,
            candidate_limit=settings.candidate_limit,
            buffer_minutes=settings.buffer_minutes,
        )
        self.arranger = ArrangerAgent(
            self.rooms,
            self.calendar,
            self.audit,
            spend_limit_yen=settings.agent_spend_limit_yen,
        )
        self.notifier = NotifierAgent(self.chat, self.notifications, self.audit)
        self.follower = FollowerAgent(self.transit, self.calendar, self.audit, self.llm)
        self.orchestrator = OrchestratorAgent(
            repository=self.repository,
            audit=self.audit,
            llm=self.llm,
            chat=self.chat,
            optimizer=self.optimizer,
            arranger=self.arranger,
            notifier=self.notifier,
            follower=self.follower,
        )

    def providers(self) -> dict[str, str]:
        return {
            "transit": self.transit.name,
            "rooms": self.rooms.name,
            "calendar": self.calendar.name,
            "chat": "in_app",
            "notifications": "in_app",
            "llm": self.llm.name,
            "repository": self.repository.name,
        }


@lru_cache
def get_container() -> Container:
    return Container(get_settings())
