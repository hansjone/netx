"""Shared test hygiene: never connect the test suite to the application database."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


# Set this before any test imports netx_api.config/db. Several older unittest
# cases use the global SessionLocal and delete entire tables in setUp.
_TEST_STATE = TemporaryDirectory(prefix="netx-tests-", ignore_cleanup_errors=True)
_TEST_DATABASE_URL = f"sqlite:///{(Path(_TEST_STATE.name) / 'netx.sqlite').as_posix()}"
os.environ["NETX_DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["NETX_CREDENTIAL_SECRET_FILE"] = str(Path(_TEST_STATE.name) / "credential_secret")
# Login bootstraps the MCP token file; relative defaults would land in the
# real data/ directory and break the running API's MCP token.
for _key, _rel in {
    "NETX_AUTH_SECRET_FILE": "auth/jwt_secret",
    "NETX_AUTH_MCP_TOKEN_FILE": "auth/mcp_token",
    "NETX_NE_COLLECTION_DATA_DIR": "ne_collections",
    "NETX_BIZ_STATE_SPOOL_DIR": "biz_state_spool",
    "NETX_NE_EXEC_JOB_DIR": "ne_exec_jobs",
    "NETX_WEBCRT_DATA_DIR": "webcrt",
    "NETX_SCHEDULER_HEARTBEAT_PATH": "runtime/scheduler_heartbeat.json",
}.items():
    os.environ[_key] = str(Path(_TEST_STATE.name) / _rel)


def pytest_sessionstart(session):
    from sqlalchemy.engine import make_url

    from netx_api.db import engine

    # Compare parsed URLs: newer SQLAlchemy renders "C:" as "C%3A" in str(url).
    if engine.url != make_url(_TEST_DATABASE_URL):
        raise pytest.UsageError("NetX tests require the isolated temporary database")


def pytest_sessionfinish(session, exitstatus):
    from netx_api.db import engine

    engine.dispose()
    _TEST_STATE.cleanup()


@pytest.fixture(autouse=True)
def _fresh_baseline_row_cache():
    """Test databases reuse batch ids; the process-wide baseline cache must not leak between tests."""
    from netx_api.biz_migration.service import BASELINE_ROWS

    BASELINE_ROWS.invalidate()
    yield
    BASELINE_ROWS.invalidate()
