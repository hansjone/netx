"""Connect-failure detail formatting should stay short and non-redundant."""

from __future__ import annotations

from netx_api.ne_connect import (
    _exc_headline,
    _format_failure_detail,
    _is_network_reachability_fail,
    _root_cause_headline,
)


def test_exc_headline_uses_first_line_only() -> None:
    exc = Exception("TCP connection to device failed.\n\nCommon causes of this problem are:\n1. wrong IP")
    assert _exc_headline(exc) == "Exception: TCP connection to device failed."
    assert "Common causes" not in _exc_headline(exc)


def test_root_cause_headline() -> None:
    try:
        try:
            raise TimeoutError("[WinError 10060] timed out")
        except TimeoutError as inner:
            raise RuntimeError("TCP connection to device failed.") from inner
    except RuntimeError as outer:
        root = _root_cause_headline(outer)
        assert root is not None
        assert "TimeoutError" in root
        assert "10060" in root


def test_format_failure_detail_omits_stack_for_timeout() -> None:
    creds = {
        "ip_address": "192.168.0.127",
        "port": 22,
        "protocol": "ssh",
        "device_type": "huawei",
        "vendor": "Huawei",
        "username": "huawei",
        "hop_enabled": False,
    }
    try:
        try:
            raise TimeoutError("[WinError 10060] timed out")
        except TimeoutError as inner:
            raise RuntimeError(
                "TCP connection to device failed.\n\nCommon causes of this problem are:\n1. firewall"
            ) from inner
    except RuntimeError as outer:
        assert _is_network_reachability_fail(outer)
        detail = _format_failure_detail(creds, outer)

    assert "target=192.168.0.127:22/ssh" in detail
    assert "hop=disabled (direct)" in detail
    assert "result=fail" in detail
    assert "error=RuntimeError: TCP connection to device failed." in detail
    assert "cause=TimeoutError:" in detail
    assert "Common causes" not in detail
    assert "Traceback" not in detail
    assert "stack:" not in detail
    assert "File \"" not in detail
