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


@pytest.fixture
def commented_gs_gt_path() -> Path:
    """Return the path of the ``--GS``/``--GT`` commented-record fixture."""
    return FIXTURES_DIR / "commented_gs_gt.rw5"


@pytest.fixture
def stored_points_path() -> Path:
    """Return the path of the ``SP`` stored-point fixture."""
    return FIXTURES_DIR / "stored_points.rw5"


@pytest.fixture
def south_cube_ep_bl_gs_path() -> Path:
    """Return the path of the South/Cube ``EP``/``BL``/``GS`` fixture."""
    return FIXTURES_DIR / "south_cube_ep_bl_gs.rw5"


@pytest.fixture
def base_configuration_path() -> Path:
    """Return the SurvCE base-configuration + HDOP Min:/Max: fixture."""
    return FIXTURES_DIR / "base_configuration.rw5"


@pytest.fixture
def offset_local_time_path() -> Path:
    """Return the offset-shot fixture with ``DT``/``TM`` but no ``G0``/``GT``."""
    return FIXTURES_DIR / "offset_local_time.rw5"


@pytest.fixture
def offset_g0_moment_path() -> Path:
    """Return the offset-shot fixture with ``G0`` moment but no ``GT``."""
    return FIXTURES_DIR / "offset_g0_moment.rw5"
