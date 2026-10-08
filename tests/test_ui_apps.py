"""Smoke test headless delle due app Streamlit (AppTest)."""
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.journal import read_journal
from src.pipeline import run_pipeline
from tests.conftest import DATE, ROOT, opts


@pytest.fixture()
def seeded(project, monkeypatch):
    monkeypatch.setenv("OFO_ROOT", str(project.root))
    st.cache_resource.clear()
    run_pipeline(opts(project))
    yield project
    st.cache_resource.clear()


def no_exc(at):
    assert not at.exception, [e.value for e in at.exception]


def test_standard_app_renders_all_sections(seeded):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    no_exc(at)
    assert len(at.tabs) == 10
    labels = [t.label for t in at.tabs]
    for want in ("Dashboard", "Market Data", "Agent Council", "Scenarios", "Risk", "Judge", "PANIC-PROOF",
                 "Historical Reports", "Journal", "Scoreboard"):
        assert want in labels
    assert any("AI COUNCIL VERDICT" in s.value for s in at.subheader)


def test_standard_app_without_reports_is_usable(project, monkeypatch):
    monkeypatch.setenv("OFO_ROOT", str(project.root))
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    no_exc(at)


def test_pixel_app_renders_and_embeds_room(seeded):
    at = AppTest.from_file(str(ROOT / "app_pixel.py"), default_timeout=60).run()
    no_exc(at)
    assert any("PANIC-PROOF" in s.value for s in at.subheader)
    assert any("HUMAN DECISION" in s.value for s in at.subheader)


def test_pixel_app_empty_state(project, monkeypatch):
    monkeypatch.setenv("OFO_ROOT", str(project.root))
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app_pixel.py"), default_timeout=60).run()
    no_exc(at)
    assert at.info


def test_human_decision_button_records_separately(seeded):
    at = AppTest.from_file(str(ROOT / "app_pixel.py"), default_timeout=60).run()
    before = read_journal(seeded.path("journal"))[0]
    assert before["human_decision"] == "PENDING"
    next(b for b in at.button if b.label == "SKIP").click().run()
    no_exc(at)
    row = read_journal(seeded.path("journal"))[0]
    assert row["human_decision"] == "SKIP" and row["council_status"] == before["council_status"]


def test_run_button_executes_mock_analysis(project, monkeypatch):
    monkeypatch.setenv("OFO_ROOT", str(project.root))
    st.cache_resource.clear()
    at = AppTest.from_file(str(ROOT / "app_pixel.py"), default_timeout=90).run()
    next(b for b in at.sidebar.button if "Esegui" in b.label).click().run()
    no_exc(at)
    assert (project.path("reports_dir") / f"{DATE}.json").exists()
