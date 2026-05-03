from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NETX_", extra="ignore")

    database_url: str = "postgresql+psycopg://netx:netx@127.0.0.1:5432/netx"
    host: str = "127.0.0.1"
    port: int = 8890
    vendor: str = "ZTE"
    source_type: str = "gateway_export_excel"
    parser_config: str = "netx_api/config/parsers/zte_alarm_monitor_v1.yaml"
    frontend_url: str = "http://127.0.0.1:5173"
    oclaw_analyze_url: str = "http://127.0.0.1:8787/admin/api/ops-ai/analyze-sync"
    oclaw_analyze_token: str = ""
    oclaw_health_url: str = "http://127.0.0.1:8787/admin/api/ops-ai/health"
    # analyze-sync runs a full gateway + LLM turn; 35s is often too short (was hardcoded).
    oclaw_connect_timeout_sec: float = 15.0
    oclaw_analyze_read_timeout_sec: float = 180.0
    oclaw_health_timeout_sec: float = 8.0


settings = Settings()
