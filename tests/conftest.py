"""Shared pytest fixtures for siscadro-rw5 tests.

Every fixture file under ``tests/fixtures`` is synthetic; none of them
contain customer names, coordinates, or files found on a personal data
drive.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the directory containing the synthetic ``.rw5`` fixtures."""
    return FIXTURES_DIR


@pytest.fixture
def english_minimal_path() -> Path:
    """Return the path of the English-locale minimal fixture."""
    return FIXTURES_DIR / "english_minimal.rw5"


@pytest.fixture
def romanian_minimal_path() -> Path:
    """Return the path of the Romanian-locale minimal fixture."""
    return FIXTURES_DIR / "romanian_minimal.rw5"


@pytest.fixture
def quality_and_offsets_path() -> Path:
    """Return the path of the offsets/quality/stakeout fixture."""
    return FIXTURES_DIR / "quality_and_offsets.rw5"
