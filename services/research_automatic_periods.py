"""Conservative account-wide protection against recycled automatic final dates.

The registered scanner metadata migration creates the additive table. Claims are
durable even after cancellation: once search can start, the chronology is not
relabelled untouched. The old evidence itself is never changed.
"""

import time

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database.research_db import ResearchAutomaticPeriodUse, ResearchExperiment, ResearchJob
from services import scanner_research_service as service

MAX_ROWS = 100000
MAX_PRIOR_JOBS = 1000


def check_available(store, owner, evidence):
    """Read-only early admission; the fenced claim still owns final reservation."""
    claim(store, owner, None, evidence, None, dry_run=True)


def claim(store, owner, inputs_artifact, evidence, job_id, *, dry_run=False):
    periods = evidence["automatic_recipe"]["periods"]
    final = periods["final"]
    days = [
        day
        for day in evidence["snapshot"]["sessions"]
        if periods["search"]["from"] <= day <= final["to"]
    ]
    if not days or len(days) > 3000:
        raise ValueError("Automatic research needs bounded recorded evaluation dates")
    try:
        session_scope = store.sessions if dry_run else store.sessions.begin
        with session_scope() as db:
            if not dry_run:
                service.write_guard(db)
            existing = db.scalars(
                select(ResearchAutomaticPeriodUse).where(
                    ResearchAutomaticPeriodUse.owner == owner,
                    ResearchAutomaticPeriodUse.day >= days[0],
                    ResearchAutomaticPeriodUse.day <= days[-1],
                )
            ).all()
            by_day = {row.day: row for row in existing}
            # An exact resume is idempotent. New attempts cannot recycle final
            # observations even with another source name, recipe or price hash.
            for day in days:
                row = by_day.get(day)
                is_final = final["from"] <= day <= final["to"]
                if (
                    row
                    and (row.job_id != job_id or row.inputs_artifact != inputs_artifact)
                    and (is_final or row.role == "final")
                ):
                    raise ValueError(
                        f"Final test dates {final['from']} to {final['to']} were already used "
                        "in research. Choose Historical optimisation to reuse dates without "
                        "independent validation, or use genuinely new later data."
                    )
            if all(
                by_day.get(day)
                and by_day[day].inputs_artifact == inputs_artifact
                and by_day[day].job_id == job_id
                for day in days
            ):
                return
            # Old jobs predate claims. Their published outcomes are already
            # available, so none of those dates can now become an untouched final.
            jobs = db.scalars(
                select(ResearchJob)
                .join(ResearchExperiment, ResearchExperiment.job_id == ResearchJob.id)
                .where(
                    ResearchJob.owner == owner,
                    ResearchJob.status == "completed",
                    ResearchExperiment.kind.in_(["portfolio_backtest", "portfolio_optimize"]),
                )
                .limit(MAX_PRIOR_JOBS + 1)
            ).all()
            if len(jobs) > MAX_PRIOR_JOBS:
                raise ValueError(
                    "Too much earlier evidence to certify unused dates. Review the research history first."
                )
            for job in jobs:
                bundle = service.read_artifact(store, job.result_artifact)
                if job.id == job_id:
                    continue
                reports = [bundle["result"]]
                if reports[0].get("validation", {}).get("result"):
                    reports.append(reports[0]["validation"]["result"])
                for report in reports:
                    observations = [row["date"] for row in report.get("equity_curve", [])]
                    coverage = report.get("coverage", {})
                    first = min(observations) if observations else coverage.get("date_from")
                    last = max(observations) if observations else coverage.get("date_to")
                    if not first or not last:
                        raise ValueError(
                            "An earlier report has unknown dates. Review its evidence before reserving a final period."
                        )
                    if first <= final["to"] and last >= final["from"]:
                        raise ValueError(
                            f"Final test dates {final['from']} to {final['to']} already have "
                            "published research outcomes. Choose Historical optimisation to "
                            "reuse dates without independent validation, or use new later data."
                        )
            if dry_run:
                return
            new_days = [day for day in days if day not in by_day]
            count = db.scalar(select(func.count()).select_from(ResearchAutomaticPeriodUse))
            if count + len(new_days) > MAX_ROWS:
                raise ValueError("Saved research period history reached its storage limit")
            for day in new_days:
                db.add(
                    ResearchAutomaticPeriodUse(
                        owner=owner,
                        day=day,
                        inputs_artifact=inputs_artifact,
                        job_id=job_id,
                        role="final" if final["from"] <= day <= final["to"] else "development",
                        created_at=time.time(),
                    )
                )
    except IntegrityError as error:
        # A competing worker won the durable claim. No simulation has run yet.
        raise ValueError(
            "Another research run just reserved these dates. Resume after reviewing it."
        ) from error
