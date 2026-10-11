"""Large categorical research cannot block completion in fANOVA integration."""

from copy import deepcopy

import optuna
import pytest
from test_study_analysis import experiment

from research.study_analysis import MAX_FANOVA_CATEGORIES, build_study_analysis


@pytest.mark.parametrize("scope", ["indicators", "relative", "joint"])
def test_large_hypothesis_study_skips_fanova_before_fit_and_keeps_native_charts(monkeypatch, scope):
    if scope == "joint":
        from research.joint_stock_market import catalog
    elif scope == "relative":
        from research.stock_relative_strength import catalog
    else:
        from research.stock_indicators import catalog

    original = experiment()
    choices = [row["id"] for row in catalog()]
    original["search_space"]["axes"]["hypothesis"] = {"type": "categorical", "choices": choices}
    for index, row in enumerate(original["trials"]):
        row["params"]["hypothesis"] = choices[index % len(choices)]
    unchanged = deepcopy(original)
    monkeypatch.setattr(
        optuna.importance,
        "FanovaImportanceEvaluator",
        lambda *a, **k: pytest.fail("High-cardinality fANOVA must not fit or integrate"),
    )
    progress = []
    result = build_study_analysis(
        original, progress=lambda done, total: progress.append((done, total))
    )
    charts = {row["id"]: row for row in result["charts"]}
    assert charts["importance"]["status"] == "unavailable"
    assert f"hypothesis: {len(choices)} choices" in charts["importance"]["reason"]
    assert str(MAX_FANOVA_CATEGORIES) in charts["importance"]["reason"]
    assert "figure" not in charts["importance"]
    assert charts["history"]["status"] == charts["slice"]["status"] == "available"
    assert charts["parallel"]["status"] == charts["edf"]["status"] == "available"
    assert progress[-1] == (12, 12)
    assert original == unchanged


def test_small_category_study_keeps_the_existing_seeded_native_fanova_method():
    original = experiment()
    choices = [f"choice{i}" for i in range(MAX_FANOVA_CATEGORIES)]
    original["search_space"]["axes"]["choice"] = {"type": "categorical", "choices": choices}
    for index, row in enumerate(original["trials"]):
        row["params"]["choice"] = choices[index % len(choices)]
    result = build_study_analysis(original)
    importance = next(chart for chart in result["charts"] if chart["id"] == "importance")
    assert importance["status"] == "available"
    assert importance["figure"]["data"]
    assert any("32 trees, depth 8" in line for line in result["basis"])
