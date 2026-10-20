import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentiment import config, store  # noqa: E402


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.delenv("JUDGE_BACKEND", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    return config.load(ROOT / "config.yaml")


@pytest.fixture
def con(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()
