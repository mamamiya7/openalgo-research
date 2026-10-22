"""Recorded v16 execution dimensions survive the optional plotting subprocess."""

from copy import deepcopy

import pytest
from test_portfolio_allocation_analysis import recorded

from research import study_diagnostics as diagnostics
from research.study_analysis import parameter_limit, reconstruct_study


def test_v16_payload_preserves_execution_descriptor_and_reader_accepts_all_dimensions(monkeypatch):
    original = recorded()
    original["account"] = {"private": "excluded"}
    original["selected_reports"] = {"equity": "excluded"}
    before = deepcopy(original)
    monkeypatch.setattr(
        "research.connectors.vectorbt_portfolio.evaluate",
        lambda *_a, **_kw: pytest.fail("Reading study parameters cannot calculate an account"),
    )
    request = diagnostics.payload(original, ["portfolio.allocation_mode"])
    assert request["experiment"]["execution_research"] == original["execution_research"]
    assert request["parameters"] == ["portfolio.allocation_mode"]
    assert "account" not in request["experiment"]
    assert "selected_reports" not in request["experiment"]
    _, distributions = reconstruct_study(request["experiment"])
    assert len(distributions) == parameter_limit("automatic-hypotheses-v16") == 75
    assert original == before


def test_v16_76th_axis_is_rejected_before_a_child_can_start(monkeypatch, tmp_path):
    original = recorded()
    original["search_space"]["axes"]["overflow.target_pct"] = {"min": 1, "max": 2, "step": 1}
    monkeypatch.setattr(
        diagnostics.subprocess,
        "Popen",
        lambda *_a, **_kw: pytest.fail("An oversized study started a process"),
    )
    with pytest.raises(ValueError, match="proposal/parameter limits"):
        diagnostics.build(original, directory=tmp_path)
    assert not list(tmp_path.iterdir())


def test_v15_transport_keeps_its_prior_74_axis_limit():
    original = recorded()
    original["experiment_version"] = "automatic-hypotheses-v15"
    assert parameter_limit(original["experiment_version"]) == 74
    with pytest.raises(ValueError, match="proposal/parameter limits"):
        diagnostics.payload(original, None)
    extra = next(name for name in original["search_space"]["axes"] if name.startswith("numeric"))
    original["search_space"]["axes"].pop(extra)
    assert len(diagnostics.payload(original, None)["experiment"]["search_space"]["axes"]) == 74


def test_real_isolated_child_accepts_v16_execution_descriptor_and_reaps_cleanly(tmp_path):
    # Zero completed trials is a supported sparse study, not a fabricated account.
    # It exercises both process-boundary admissions and the actual native reader.
    original = recorded()
    before = deepcopy(original)
    result = diagnostics.build(
        original, parameters=["portfolio.allocation_mode"], directory=tmp_path
    )
    assert result["version"] == "research-analysis-v1"
    assert len(result["charts"]) == 12
    assert all(chart["status"] == "unavailable" for chart in result["charts"])
    assert original == before
    assert not list(tmp_path.iterdir())
