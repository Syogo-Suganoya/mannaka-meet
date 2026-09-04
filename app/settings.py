"""環境変数による設定。プロバイダ名を切り替えるだけでモック↔実APIが入れ替わる。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"
    port: int = 8080

    # 差し替えスイッチ
    transit_provider: Literal["mock", "ekispert"] = "mock"
    repository: Literal["firestore", "memory"] = "firestore"
    llm_provider: Literal["stub", "gemini"] = "stub"

    # 実API接続情報（差し替え時のみ必要）
    ekispert_api_key: str = ""
    ekispert_base_url: str = "https://api.ekispert.jp/v1/json"
    google_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    google_cloud_project: str = "mannaka-local"

    # 評価のパラメータ
    candidate_limit: int = 8
    buffer_minutes: int = 10  # 到着バッファ

    @property
    def uses_real_transit(self) -> bool:
        return self.transit_provider == "ekispert" and bool(self.ekispert_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
