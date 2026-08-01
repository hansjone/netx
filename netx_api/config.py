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
    oclaw_alarm_ws_enabled: bool = False
    oclaw_alarm_ws_url: str = "ws://127.0.0.1:8787/ws/netx-bridge"
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
    ume_marker_page_limit: int = 1000
    ume_marker_max_pages: int = 2000
    ume_iterator_500_as_end: bool = True
    ume_auth_header: str = "accessToken"
    ume_content_type: str = "application/yang-data+json;charset=UTF-8"
    ume_token_ttl_s: int = 1800
    ume_token_refresh_skew_s: int = 60
    ume_keepalive_enabled: bool = True
    ume_keepalive_interval_s: int = 600
    ume_keepalive_renew_before_s: int = 900
    ume_sync_alarms_current_enabled: bool = True
    ume_sync_alarms_current_interval_s: int = 18000
    ume_sync_alarms_current_skip_when_ws: bool = True
    # Block WSS until initial REST current-alarm snapshot finishes (see startup gate).
    ume_startup_sync_alarms_before_ws: bool = True
    # Wait this long after process start before the initial REST snapshot (WSS still blocked).
    ume_startup_alarm_sync_delay_s: int = 60
    ume_alarm_cleared_tombstone_s: int = 300
    ume_alarm_ws_enabled: bool = True
    ume_notification_establish_path: str = "/restconf/operations/zte-notifications:establish-subscription"
    ume_notification_delete_path: str = "/restconf/operations/zte-notifications:delete-subscription"
    ume_notification_topic: str = "ALARM"
    ume_sync_inventory_auto_enabled: bool = True
    ume_sync_inventory_every_hours: int = 48
    ume_token_path: str = "/restconf/operations/zte-security:oauth_token"
    ume_token_handshake_path: str = "/restconf/operations/zte-security:oauth_handshake"
    ume_token_logout_path: str = "/restconf/operations/zte-security:oauth_token"
    ume_ne_path: str = "/restconf/data/zte-resources-module:network-elements"
    ume_alarms_path: str = "/restconf/data/zte-alarms:alarms/alarm-list"
    ume_sync_alarms_history_every_hours: int = 24
    # Managed NE credentials (Fernet key; generate with cryptography.fernet.Fernet.generate_key())
    credential_secret_key: str = ""
    ne_connect_max_workers: int = 5
    ne_connect_timeout_sec: int = 30
    ne_collect_max_workers: int = 5
    ne_collect_read_timeout_sec: int = 120
    ne_collect_stale_run_sec: int = 900
    ne_collect_pending_stale_sec: int = 180
    ne_collect_run_timeout_cap_sec: int = 600
    ne_collection_data_dir: str = "data/ne_collections"
    # Config sync (periodic running-config backup into DB)
    config_sync_scheduler_enabled: bool = True
    config_sync_scheduler_tick_sec: int = 60
    # After process start / unexpected restart, wait before any scheduled sync.
    config_sync_startup_grace_sec: int = 3600
    # Port traffic monitoring (CLI rate bit/s samples)
    port_traffic_scheduler_enabled: bool = True
    port_traffic_scheduler_tick_sec: int = 15
    # Managed NE exec: max CLI commands per request (lab can raise; hard-capped in ne_exec).
    ne_exec_max_commands: int = 5
    # WebCRT interactive terminal sessions
    webcrt_max_sessions: int = 20
    webcrt_idle_timeout_sec: int = 1800
    webcrt_connect_timeout_sec: int = 90
    webcrt_attach_timeout_sec: int = 60
    webcrt_data_dir: str = "data/webcrt"
    # SSH transport keepalive interval (seconds); 0 disables.
    webcrt_keepalive_sec: int = 30
    # Device anti-idle CLI nudge (0 = off). Keep off: NEs close idle VTY themselves.
    webcrt_anti_idle_sec: int = 0
    webcrt_anti_idle_payload: str = " "
    # Cap stdout queue depth (drop oldest when full) to protect memory.
    webcrt_out_queue_max: int = 2000
    # Persist per-session transcripts under webcrt_data_dir/sessions/.
    webcrt_session_log_enabled: bool = True
    # Reader: short blocking wait instead of fixed 40ms spin (seconds).
    webcrt_reader_poll_sec: float = 0.01
    # Local app login / audit (lab defaults; override in production)
    auth_enabled: bool = True
    # Stable default so JWT survives restarts without .env. Override in production.
    auth_secret: str = "netx-dev-auth-secret-change-me-in-production-32b"
    auth_token_ttl_sec: int = 86400
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"
    # Written on first boot for MCP; path relative to cwd / absolute
    auth_mcp_token_file: str = "data/auth/mcp_token"


settings = Settings()
