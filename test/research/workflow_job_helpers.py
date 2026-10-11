"""Explicit bounded completion of optional jobs in isolated workflow tests."""

from sqlalchemy import func, select

from database.research_db import ResearchExperiment, ResearchJob
from services import scanner_research_worker as worker

ACTIVE = ("queued", "running", "cancelling", "pausing")


def numerical_job_count(store):
    """Count financial calculations, independently of optional report analysis."""
    with store.sessions() as db:
        return db.scalar(
            select(func.count())
            .select_from(ResearchJob)
            .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
            .where(ResearchExperiment.kind.in_(("portfolio_backtest", "portfolio_optimize")))
        )


def complete_optional_jobs(store, *, token="workflow-optional", limit=16):
    """Run real queued analysis jobs to completion before asserting queue idle.

    Refuse unrelated pending numerical work instead of accidentally evaluating
    another strategy. Maintenance's actual idle requirement remains unchanged.
    This helper never manufactures completion or suppresses chart generation.
    """
    assert type(limit) is int and 1 <= limit <= 16

    def pending():
        with store.sessions() as db:
            rows = db.execute(
                select(ResearchJob.id, ResearchJob.status, ResearchExperiment.kind)
                .outerjoin(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
                .where(ResearchJob.status.in_(ACTIVE))
            ).all()
        assert all(row.kind == "portfolio_analysis" and row.status == "queued" for row in rows), (
            "Optional completion requires only queued analysis work; pending jobs: " + repr(rows)
        )
        assert len(rows) <= limit, "Optional queue exceeds the explicit test completion bound"
        return rows

    queued = pending()
    if not queued:
        return []
    ids = [row.id for row in queued]
    worker.acquire(store, token)
    try:
        for _ in range(limit):
            if not pending():
                break
            assert worker.run_one(store, token), "Queued optional analysis was not claimed"
        assert not pending(), "Optional analysis did not finish within the test worker bound"
    finally:
        worker.release(store, token)
    with store.sessions() as db:
        jobs = [db.get(ResearchJob, job_id) for job_id in ids]
        assert all(job.status == "completed" for job in jobs), [
            (job.id, job.status, job.error) for job in jobs
        ]
        return jobs


def complete_optional_job(store, job_id, *, token="workflow-optional", limit=16):
    """Return one completed analysis job, allowing earlier optional queue entries."""
    with store.sessions() as db:
        experiment = db.get(ResearchExperiment, job_id)
        assert experiment is not None and experiment.kind == "portfolio_analysis"
    complete_optional_jobs(store, token=token, limit=limit)
    with store.sessions() as db:
        job = db.get(ResearchJob, job_id)
        assert job is not None, job_id
        assert job.status == "completed", (job_id, job.status, job.error)
        return job
