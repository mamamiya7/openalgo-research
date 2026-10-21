"""Actual service retention, Optuna/native gates and controlled account outcomes."""

# ruff: noqa: F811

import pytest
from test_jobs import app, client  # noqa: F401
from test_portfolio_sensitivity_integration import exercise_full_service
from test_recommendation_seeds import prepared

pytestmark = pytest.mark.timeout(700)


def test_full_seed_service_pause_recovery_and_immutable_support(app, client, monkeypatch):
    exercise_full_service(app, client, monkeypatch, prepared())
