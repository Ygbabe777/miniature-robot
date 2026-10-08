"""Fixture condivise: progetto temporaneo con dati sintetici deterministici."""
from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest

from src.clock import FakeClock
from src.config import AppConfig, load_config
from src.pipeline import PipelineOptions
from tools.sample_data import write

ROOT = Path(__file__).resolve().parent.parent
DATE = "2026-10-08"


@pytest.fixture()
def project(tmp_path: Path) -> AppConfig:
    """Copia config/prompts in tmp e genera dati sintetici: nessun file reale viene toccato."""
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "data").mkdir()
    write(tmp_path / "data", DATE)
    return load_config(tmp_path / "config.yaml", root=tmp_path)


@pytest.fixture()
def project_no_options(tmp_path: Path) -> AppConfig:
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "data").mkdir()
    write(tmp_path / "data", DATE, with_options=False)
    return load_config(tmp_path / "config.yaml", root=tmp_path)


def opts(cfg: AppConfig, scenario: str = "ok", **kw) -> PipelineOptions:
    return PipelineOptions(date=DATE, mock=True, mock_scenario=scenario, cfg=cfg, clock=FakeClock(),
                           rng=random.Random(7), run_id="TEST-RUN", **kw)


@pytest.fixture()
def project_no_es(tmp_path: Path) -> AppConfig:
    shutil.copy(ROOT / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    (tmp_path / "data").mkdir()
    write(tmp_path / "data", DATE, with_es=False)
    return load_config(tmp_path / "config.yaml", root=tmp_path)
