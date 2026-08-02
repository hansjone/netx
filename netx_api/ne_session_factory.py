"""Netmiko session factory facade: direct, vendor CLI hop, bastion, Linux jump."""
from __future__ import annotations

from netmiko import ConnectHandler

from .ne_cli_hop import (
    cli_hop_nested_session_ended,
    cli_hop_returned_to_proxy,
    extract_cli_prompt_marker,
    get_cli_hop_guard,
    should_close_cli_hop_session,
)
from .ne_hop_templates import (
    bastion_ssh_cli,
    default_bastion_username_template,
    default_cisco_hop_template,
    default_hop_command_template,
    default_huawei_hop_template,
    default_zte_hop_template,
    render_hop_command,
    resolve_bastion_ssh_username,
)
from .ne_session_connect import (
    _bastion_interactive_handler,
    _bastion_ssh_connect,
    _build_netmiko_connection,
    _connect_direct,
    _connect_via_bastion,
    _connect_via_cli_hop,
    _connect_via_linux_hop,
    _interactive_driver_class,
    _interactive_target_auth,
    _netmiko_driver_class,
    _netmiko_over_ssh_client,
    _read_channel,
    close_netmiko_connection,
    open_netmiko_connection,
)

__all__ = [
    "ConnectHandler",
    "_bastion_interactive_handler",
    "_bastion_ssh_connect",
    "_build_netmiko_connection",
    "_connect_direct",
    "_connect_via_bastion",
    "_connect_via_cli_hop",
    "_connect_via_linux_hop",
    "_interactive_driver_class",
    "_interactive_target_auth",
    "_netmiko_driver_class",
    "_netmiko_over_ssh_client",
    "_read_channel",
    "bastion_ssh_cli",
    "cli_hop_nested_session_ended",
    "cli_hop_returned_to_proxy",
    "close_netmiko_connection",
    "default_bastion_username_template",
    "default_cisco_hop_template",
    "default_hop_command_template",
    "default_huawei_hop_template",
    "default_zte_hop_template",
    "extract_cli_prompt_marker",
    "get_cli_hop_guard",
    "open_netmiko_connection",
    "render_hop_command",
    "resolve_bastion_ssh_username",
    "should_close_cli_hop_session",
]
