"""Owner-scoped metadata links for compact portfolio result presentation.

The caller owns the existing SQLAlchemy session. All queries are bounded and
read metadata only; no artifacts, prices, external services or caches are opened.
"""

from sqlalchemy import and_, select

from database.research_db import (
    ResearchCandidateReport,
    ResearchJob,
    ResearchLibraryExperiment,
    ResearchLibraryJob,
    ResearchSetupVersion,
)


def display_links(db, job, *, experiment_id=None):
    query = (
        select(ResearchLibraryJob, ResearchSetupVersion)
        .join(
            ResearchLibraryExperiment,
            ResearchLibraryExperiment.id == ResearchLibraryJob.experiment_id,
        )
        .outerjoin(
            ResearchSetupVersion,
            and_(
                ResearchSetupVersion.id == ResearchLibraryJob.version_id,
                ResearchSetupVersion.experiment_id == ResearchLibraryJob.experiment_id,
            ),
        )
        .where(ResearchLibraryJob.job_id == job.id, ResearchLibraryExperiment.owner == job.owner)
    )
    if experiment_id is not None:
        query = query.where(ResearchLibraryJob.experiment_id == experiment_id)
    links = db.execute(query.limit(2)).all()
    # A generic saved-run endpoint cannot choose among different library versions.
    link, version = links[0] if len(links) == 1 else (None, None)
    candidate = db.scalar(
        select(ResearchCandidateReport)
        .join(ResearchJob, ResearchJob.id == ResearchCandidateReport.study_job_id)
        .where(
            ResearchCandidateReport.report_job_id == job.id,
            ResearchCandidateReport.owner == job.owner,
            ResearchJob.owner == job.owner,
            ResearchJob.result_artifact == ResearchCandidateReport.parent_result_artifact,
        )
    )
    parent_job_id = version.parent_job_id if version else None
    if parent_job_id:
        parent = db.get(ResearchJob, parent_job_id)
        if parent is None or parent.owner != job.owner:
            parent_job_id = None
    return {
        "link_role": link.role if link else None,
        "setup": {
            "id": version.id,
            "name": version.name,
            "number": version.number,
            "parent_version_id": version.parent_version_id,
        }
        if version
        else None,
        "candidate": {
            "study_job_id": candidate.study_job_id,
            "config_id": candidate.config_id,
            "period": candidate.period,
        }
        if candidate
        else None,
        "parent_job_id": candidate.study_job_id if candidate else parent_job_id,
    }
