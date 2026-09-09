# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Uploads cannot silently turn real scanner membership into synthetic prices."""

from io import BytesIO

import pytest
from sqlalchemy import func, select
from test_jobs import app, client  # noqa: F401

from database.research_db import ResearchJob, ResearchSource


@pytest.mark.parametrize("selection", [None, "", " ", "unknown", "Fixture"])
def test_upload_requires_explicit_valid_source(app, client, selection):
    data = {"file": (BytesIO(b"Date,Symbol\n2026-01-05,TEST\n"), "signals.csv")}
    if selection is not None:
        data["source"] = selection
    response = client.post("/scanner-research/api/sources", data=data)
    assert response.status_code == 400
    assert "Choose a price source" in response.json["message"]
    store = app.extensions["research_store"]
    with store.sessions() as db:
        assert db.scalar(select(func.count()).select_from(ResearchSource)) == 0
        assert db.scalar(select(func.count()).select_from(ResearchJob)) == 0
    assert not list((store.root / "artifacts").glob("*"))


def test_explicit_synthetic_demo_remains_labelled(app, client):
    response = client.post(
        "/scanner-research/api/sources",
        data={
            "file": (BytesIO(b"Date,Symbol\n2026-01-05,TEST\n"), "signals.csv"),
            "source": "fixture",
        },
    )
    assert response.status_code == 201
    assert response.json["provenance"]["synthetic"] is True
