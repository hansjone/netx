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
    ume_verify_tls: bool = True
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
    # Shared-server worker caps (sized for multi-operator use; raise if bastion/DB allow).
    ne_connect_max_workers: int = 8
    ne_connect_timeout_sec: int = 30
    ne_collect_max_workers: int = 8
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
    # Fabric LLDP collect (network topology management).
    # Scheduler thread may run, but policy.enabled defaults False — no collect until operator turns it on.
    lldp_collect_scheduler_enabled: bool = True
    lldp_collect_scheduler_tick_sec: int = 60
    lldp_collect_startup_grace_sec: int = 3600
    # Reclaim hung discover jobs (updated_at / created_at older than these).
    lldp_collect_stale_run_sec: int = 7200
    lldp_collect_pending_stale_sec: int = 300
    # Port traffic monitoring (CLI rate bit/s samples)
    port_traffic_scheduler_enabled: bool = True
    port_traffic_scheduler_tick_sec: int = 15
    # Managed NE exec: max CLI commands per request (lab can raise; hard-capped in ne_exec).
    ne_exec_max_commands: int = 5
    # WebCRT interactive terminal sessions (multi-operator concurrent terminals).
    webcrt_max_sessions: int = 40
    webcrt_idle_timeout_sec: int = 1800
    webcrt_connect_timeout_sec: int = 90
    webcrt_attach_timeout_sec: int = 60
    # Keep device PTY after WS drop so the UI can re-attach (reconnect / remount).
    webcrt_detach_grace_sec: int = 120
    webcrt_data_dir: str = "data/webcrt"
    # SSH transport keepalive (seconds); 30s recommended behind NAT/firewall.
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
    # WebCRT SFTP transfer limits (streamed; default 512 MiB per file).
    webcrt_sftp_max_file_bytes: int = 512 * 1024 * 1024
    webcrt_sftp_chunk_bytes: int = 64 * 1024
    # Cap directory listings so huge folders cannot pin the API/UI.
    webcrt_sftp_list_max_entries: int = 5000
    webcrt_sftp_list_timeout_sec: float = 30.0
    # Local app login / audit (lab defaults; override in production)
    auth_enabled: bool = True
    # Empty = auto-generate & persist to auth_secret_file (recommended).
    # Set explicitly only when you want a shared/ops-managed secret.
    auth_secret: str = ""
    auth_secret_file: str = "data/auth/jwt_secret"
    auth_token_ttl_sec: int = 86400
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"
    # Written on first boot for MCP; path relative to cwd / absolute
    auth_mcp_token_file: str = "data/auth/mcp_token"
    # Expose /docs /redoc /openapi.json without auth when true (lab only).
    docs_enabled: bool = False
    # Refuse start when bind host is non-loopback and insecure defaults remain.
    allow_insecure_defaults: bool = False
    # Async audit writer; sample_n>1 keeps 1/N of generic http.* events.
    audit_async: bool = True
    audit_sample_n: int = 5
    # Prefer Alembic on API start; brownfield patches live in schema_patches + revisions.
    # Auth column ensures still run as a safety net before bootstrap.
    skip_legacy_startup_ddl: bool = True
    # Run `alembic upgrade head` during API startup (recommended default).
    alembic_upgrade_on_start: bool = True
    # Optional dedicated SQLAlchemy URL for /v1/sql/* (read-only DB role recommended).
    sql_readonly_database_url: str = ""
    # When true (default), API also runs config_sync / lldp / port_traffic schedulers.
    # Production split: set false and run `python -m netx_api.worker` beside the API.
    run_inline_schedulers: bool = True
    # SQLAlchemy QueuePool for multi-user API + collectors + UME WS.
    # Rule of thumb: pool_size + max_overflow >= HTTP/WS peak + cli_max_concurrent + sidebands.
    db_pool_size: int = 40
    db_max_overflow: int = 40
    db_pool_recycle_sec: int = 1800
    db_pool_timeout_sec: int = 30
    # Global Netmiko/SSH concurrency across discover / collect / config_sync / port_traffic.
    cli_max_concurrent: int = 24
    # Per-feature concurrency hard ceiling (API body / policy cannot exceed this).
    cli_feature_hard_cap: int = 32
    # Shared timeout watchdog pool (not per-task executors).
    cli_timeout_pool_workers: int = 24
    # Port-traffic: how many devices may collect in parallel on the scheduler tick.
    port_traffic_dispatch_workers: int = 6
    # Bound async audit queue; drop oldest when full to protect RSS.
    audit_queue_max: int = 5000
    # Cap NE collection output files (bytes); 0 = unlimited (not recommended).
    ne_collect_max_output_bytes: int = 8 * 1024 * 1024
    # WebCRT session transcript rotate size (bytes); 0 disables rotate.
    webcrt_session_log_max_bytes: int = 4 * 1024 * 1024
    # oclaw alarm forwarder: requeue attempts before drop on send failure.
    oclaw_forward_max_retries: int = 3
    oclaw_forward_queue_max: int = 5000


settings = Settings()
