import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `import app` works without installing

from app import db  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A fresh seeded workspace database; every module reads BOARDBOT_DB, so they all see this one."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("BOARDBOT_DB", str(path))
    monkeypatch.setenv("BOARDBOT_TODAY", "2026-10-06")
    db.seed(path)
    return path
