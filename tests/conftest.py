"""Shared test hygiene."""
import pytest


@pytest.fixture(autouse=True)
def _fresh_baseline_row_cache():
    """Test databases reuse batch ids; the process-wide baseline cache must not leak between tests."""
    from netx_api.biz_migration.service import BASELINE_ROWS

    BASELINE_ROWS.invalidate()
    yield
    BASELINE_ROWS.invalidate()
