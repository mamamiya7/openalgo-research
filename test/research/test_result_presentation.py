# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Calendar diagnostics use saved coverage without rewriting exact evidence."""

import copy
import hashlib

import pytest
from test_jobs import app, client, source, submit  # noqa: F401

from database.research_db import ResearchJob
from services import scanner_research_service as service
from services.research_result_presentation import CALENDAR_REASON, present_result


def report(signal_date="2026-09-11"):
    days = ["2026-01-21", "2026-01-23", "2026-01-27", "2026-09-10"]
    return {
        "coverage": {"date_from": days[0], "date_to": days[-1], "session_count": len(days)},
        "equity_curve": [{"date": day, "equity": 10000} for day in days],
        "summary": {"net_pnl": 0, "excluded_signals": 1},
        "ledger": [
            {
                "symbol": "PAYTM",
                "signal_date": signal_date,
                "status": "excluded",
                "reason": CALENDAR_REASON,
                "quantity": 0,
                "pnl": None,
            },
            {"symbol": "OTHER", "status": "skipped", "reason": "Insufficient cash", "pnl": 0},
        ],
    }


@pytest.mark.parametrize(
    ("signal_date", "message"),
    [
        (
            "2026-09-11",
            "Signal date 11 Sep 2026 is after this run's calendar. Expected a recorded trading session from 21 Jan 2026 to 10 Sep 2026.",
        ),
        (
            "2026-01-20",
            "Signal date 20 Jan 2026 is before this run's calendar. Expected a recorded trading session from 21 Jan 2026 to 10 Sep 2026.",
        ),
        (
            "2026-01-26",
            "Signal date 26 Jan 2026 is not a recorded trading session in this run. Expected a recorded trading session from 21 Jan 2026 to 10 Sep 2026. Nearest recorded sessions: 23 Jan 2026 and 27 Jan 2026.",
        ),
    ],
)
def test_actual_signal_date_versus_saved_calendar(signal_date, message):
    original = report(signal_date)
    retained = copy.deepcopy(original)
    view = present_result(original)
    assert view["ledger"][0]["reason"] == message
    assert original == retained
    view["ledger"][0]["reason"] = CALENDAR_REASON
    assert view == original  # Every result value except display text is exact.


def test_later_period_uses_its_own_calendar_not_parent_or_csv_range():
    original = report()
    later = report("2026-09-02")
    later["coverage"] = {"date_from": "2026-09-03", "date_to": "2026-09-10", "session_count": 2}
    later["equity_curve"] = [{"date": "2026-09-03"}, {"date": "2026-09-10"}]
    original["validation"] = {"result": later, "test_from": "2026-09-01"}
    view = present_result(original)
    reason = view["validation"]["result"]["ledger"][0]["reason"]
    assert "2 Sep 2026 is before" in reason
    assert "from 3 Sep 2026 to 10 Sep 2026" in reason
    assert later["ledger"][0]["reason"] == CALENDAR_REASON


def test_trimmed_minute_curve_does_not_invent_nearest_calendar_sessions():
    original = report("2026-01-26")
    original["equity_curve"] = [{"date": "2026-01-21"}, {"date": "2026-09-10"}]
    reason = present_result(original)["ledger"][0]["reason"]
    assert "26 Jan 2026 is not a recorded trading session" in reason
    assert "21 Jan 2026 to 10 Sep 2026" in reason
    assert "Nearest" not in reason


@pytest.mark.parametrize("coverage", [{}, {"date_from": "bad", "date_to": "2026-09-10"}])
def test_old_incomplete_metadata_keeps_signal_date_without_guessing_bounds(coverage):
    original = report()
    original["coverage"] = coverage
    reason = present_result(original)["ledger"][0]["reason"]
    assert "Signal date 11 Sep 2026" in reason
    assert "has no calendar date range" in reason
    assert "21 Jan" not in reason


def test_legacy_selected_reports_and_unrelated_reasons():
    selected = report()
    original = {"experiment": {"reports": {"selected": selected}}}
    view = present_result(original)
    assert "11 Sep 2026" in view["experiment"]["reports"]["selected"]["ledger"][0]["reason"]
    assert selected["ledger"][0]["reason"] == CALENDAR_REASON
    unchanged = {"ledger": [{"reason": "Broker price unavailable: AAA at 2026-01-21"}]}
    assert present_result(unchanged) is unchanged


def test_http_enriches_existing_result_with_no_extra_reads_and_preserves_export(
    app, client, monkeypatch
):
    job_id = submit(client, source(client))
    store = app.extensions["research_store"]
    original = {"result": report(), "inputs": {"original_csv": "retained exactly"}}
    digest = service.save_artifact(store, original)
    with store.sessions.begin() as db:
        job = db.get(ResearchJob, job_id)
        job.status, job.result_artifact = "completed", digest
    before = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert before.status_code == 200
    reads = []
    read_artifact = service.read_artifact

    def read(own_store, artifact):
        reads.append(artifact)
        return read_artifact(own_store, artifact)

    monkeypatch.setattr(service, "read_artifact", read)
    response = client.get(f"/scanner-research/api/jobs/{job_id}")
    assert response.status_code == 200, response.json
    assert "11 Sep 2026 is after" in response.json["result"]["ledger"][0]["reason"]
    assert reads == [digest]
    after = client.get(f"/scanner-research/api/jobs/{job_id}/export")
    assert after.data == before.data
    assert after.headers["X-Evidence-SHA256"] == hashlib.sha256(before.data).hexdigest()
    assert read_artifact(store, digest) == original
