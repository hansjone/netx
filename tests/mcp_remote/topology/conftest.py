"""Keep layout-job persistence inside pytest temporary directories."""

import pytest


@pytest.fixture(autouse=True)
def isolated_layout_job_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NETX_LAYOUT_JOB_DIR", str(tmp_path / "layout-jobs"))
