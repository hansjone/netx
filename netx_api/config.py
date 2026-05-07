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
    # UME RESTCONF integration
    ume_base_url: str = ""
    ume_username: str = ""
    ume_password: str = ""
    ume_verify_tls: bool = False
    ume_timeout_s: float = 20.0
    ume_page_size: int = 1000
    ume_max_pages: int = 2000
    ume_limit_max: int = 5000
    ume_limit_only_page_size: int = 5000
    ume_auth_header: str = "accessToken"
    ume_content_type: str = "application/yang-data+json;charset=UTF-8"
    ume_token_ttl_s: int = 1800
    ume_token_refresh_skew_s: int = 60
    ume_keepalive_enabled: bool = True
    ume_keepalive_interval_s: int = 600
    ume_keepalive_renew_before_s: int = 900
    ume_token_path: str = "/restconf/operations/zte-security:oauth_token"
    ume_token_handshake_path: str = "/restconf/operations/zte-security:oauth_handshake"
    ume_token_logout_path: str = "/restconf/operations/zte-security:oauth_token"
    ume_ne_path: str = "/restconf/data/zte-resources-module:network-elements"
    ume_alarms_path: str = "/restconf/data/zte-alarms:alarms/alarm-list"
    ume_sync_inventory_every_hours: int = 24
    ume_sync_alarms_history_every_hours: int = 24


settings = Settings()
