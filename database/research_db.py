"""Isolated Scanner Research metadata; sessions never escape their context."""

import os
from pathlib import Path

from sqlalchemy import Boolean, Column, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

from database.engine_factory import create_db_engine

Base = declarative_base()


class ResearchSource(Base):
    __tablename__ = "research_sources"
    id = Column(String(64), primary_key=True)
    owner = Column(String(255), nullable=False, index=True)
    artifact = Column(String(64), nullable=False)
    created_at = Column(Float, nullable=False)


class ResearchJob(Base):
    __tablename__ = "research_jobs"
    id = Column(String(64), primary_key=True)
    owner = Column(String(255), nullable=False, index=True)
    source_id = Column(String(64), nullable=False)
    config = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, index=True)
    progress = Column(Integer, nullable=False, default=0)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)
    result_artifact = Column(String(64))
    error = Column(Text)
    worker = Column(String(64))


class ResearchWorker(Base):
    __tablename__ = "research_worker"
    id = Column(Integer, primary_key=True)
    token = Column(String(64))
    heartbeat = Column(Float, nullable=False, default=0)


class ResearchRequest(Base):
    """Owner-scoped retry keys; separate table preserves first-milestone rows."""

    __tablename__ = "research_requests"
    owner = Column(String(255), primary_key=True)
    token = Column(String(128), primary_key=True)
    payload_hash = Column(String(64), nullable=False)
    job_id = Column(String(64), nullable=False, index=True)


class ResearchSourceReceipt(Base):
    __tablename__ = "research_source_receipts"
    source_id = Column(String(64), primary_key=True)
    receipt = Column(Text, nullable=False)


class ResearchExperiment(Base):
    __tablename__ = "research_experiments"
    job_id = Column(String(64), primary_key=True)
    kind = Column(String(24), nullable=False)
    specification = Column(Text, nullable=False)
    identity = Column(String(64), nullable=False)
    checkpoint = Column(String(64))
    counts = Column(Text, nullable=False, default="{}")
    parent_job_id = Column(String(64))


class ResearchHistory(Base):
    __tablename__ = "research_history"
    job_id = Column(String(64), primary_key=True)
    owner = Column(String(255), nullable=False, index=True)
    test_from = Column(String(10), nullable=False)
    test_end = Column(String(10), nullable=False)
    signal_keys = Column(Text, nullable=False)


class ResearchAttempt(Base):
    __tablename__ = "research_attempts"
    job_id = Column(String(64), primary_key=True)
    previous_job_id = Column(String(64), nullable=False)
    root_job_id = Column(String(64), nullable=False, index=True)
    created_at = Column(Float, nullable=False)


class ResearchLibraryExperiment(Base):
    """Named product container; the existing job-based experiment remains exact."""

    __tablename__ = "research_library_experiments"
    id = Column(String(64), primary_key=True)
    owner = Column(String(255), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    notes = Column(Text, nullable=False, default="")
    tags = Column(Text, nullable=False, default="[]")
    pinned = Column(Boolean, nullable=False, default=False)
    archived = Column(Boolean, nullable=False, default=False)
    revision = Column(Integer, nullable=False, default=1)
    draft = Column(Text, nullable=False)
    parent_job_id = Column(String(64))
    parent_result_artifact = Column(String(64))
    parent_trial_id = Column(String(64))
    parent_version_id = Column(String(64))
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class ResearchSetupVersion(Base):
    __tablename__ = "research_setup_versions"
    __table_args__ = (UniqueConstraint("experiment_id", "number"),)
    id = Column(String(64), primary_key=True)
    experiment_id = Column(String(64), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    number = Column(Integer, nullable=False)
    draft = Column(Text, nullable=False)
    portfolio = Column(Text)
    parent_job_id = Column(String(64))
    parent_result_artifact = Column(String(64))
    parent_trial_id = Column(String(64))
    parent_version_id = Column(String(64))
    created_at = Column(Float, nullable=False)


class ResearchLibraryJob(Base):
    __tablename__ = "research_library_jobs"
    experiment_id = Column(String(64), primary_key=True)
    job_id = Column(String(64), primary_key=True)
    version_id = Column(String(64), index=True)
    role = Column(String(24), nullable=False, default="run")
    created_at = Column(Float, nullable=False)


class ResearchLibrarySource(Base):
    __tablename__ = "research_library_sources"
    experiment_id = Column(String(64), primary_key=True)
    source_id = Column(String(64), primary_key=True)
    version_id = Column(String(64), primary_key=True, default="")


class ResearchLibraryRequest(Base):
    __tablename__ = "research_library_requests"
    owner = Column(String(255), primary_key=True)
    token = Column(String(128), primary_key=True)
    payload_hash = Column(String(64), nullable=False)
    experiment_id = Column(String(64), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    version_id = Column(String(64))
    job_id = Column(String(64))


class ResearchChartinkImport(Base):
    """Acknowledged browser captures; every retry stays bound to native evidence."""

    __tablename__ = "research_chartink_imports"
    owner = Column(String(255), primary_key=True)
    request_id = Column(String(36), primary_key=True)
    payload_hash = Column(String(64), nullable=False)
    import_identity = Column(String(64), nullable=False, index=True)
    scanner_url = Column(String(512), nullable=False, index=True)
    source_id = Column(String(64), nullable=False)
    experiment_id = Column(String(64), nullable=False, index=True)
    parent_experiment_id = Column(String(64))
    created_at = Column(Float, nullable=False)


class ResearchReportPreferences(Base):
    """One bounded presentation preference document per app account."""

    __tablename__ = "research_report_preferences"
    owner = Column(String(255), primary_key=True)
    revision = Column(Integer, nullable=False)
    preferences = Column(Text, nullable=False)
    updated_at = Column(Float, nullable=False)


class ResearchCandidateReport(Base):
    """One retained report per immutable study configuration and recorded period."""

    __tablename__ = "research_candidate_reports"
    owner = Column(String(255), primary_key=True)
    study_job_id = Column(String(64), primary_key=True)
    config_id = Column(String(64), primary_key=True)
    period = Column(String(16), primary_key=True)
    parent_result_artifact = Column(String(64), nullable=False)
    report_job_id = Column(String(64), nullable=False, unique=True)
    created_at = Column(Float, nullable=False)


class ResearchShortlistCandidate(Base):
    """Editable bookmarks around exact, immutable calculation evidence."""

    __tablename__ = "research_shortlist_candidates"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "source_job_id", "source_result_artifact", "config_id", "period"
        ),
    )
    id = Column(String(32), primary_key=True)
    owner = Column(String(255), nullable=False, index=True)
    experiment_id = Column(String(64), nullable=False, index=True)
    source_job_id = Column(String(64), nullable=False, index=True)
    source_result_artifact = Column(String(64), nullable=False)
    config_id = Column(String(64), nullable=False)
    period = Column(String(16), nullable=False)
    origin_kind = Column(String(16), nullable=False)
    trial_number = Column(Integer)
    proposal_number = Column(Integer)
    is_objective_winner = Column(Boolean, nullable=False, default=False)
    snapshot = Column(Text, nullable=False)
    name = Column(String(120), nullable=False)
    note = Column(String(2000), nullable=False, default="")
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class ResearchStudyExecution(Base):
    """Observed search passes, independent of scientific checkpoint identity."""

    __tablename__ = "research_study_executions"
    id = Column(String(32), primary_key=True)
    job_id = Column(String(64), nullable=False, index=True)
    owner = Column(String(255), nullable=False, index=True)
    worker = Column(String(64), nullable=False)
    version = Column(String(40), nullable=False)
    state = Column(String(24), nullable=False)
    proposal_budget = Column(Integer, nullable=False)
    replayed = Column(Integer, nullable=False)
    started_at = Column(Float, nullable=False)
    finished_at = Column(Float)
    observed_at = Column(Float, nullable=False)
    reason_code = Column(String(32))


class ResearchStudyProposal(Base):
    """Only proposals actually observed in one pass; no synthetic replay rows."""

    __tablename__ = "research_study_proposals"
    __table_args__ = (UniqueConstraint("execution_id", "number"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(String(32), nullable=False, index=True)
    job_id = Column(String(64), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    config_id = Column(String(64), nullable=False)
    params = Column(Text, nullable=False)
    state = Column(String(24), nullable=False)
    value = Column(Float)
    reused = Column(Boolean, nullable=False, default=False)
    started_at = Column(Float, nullable=False)
    finished_at = Column(Float)
    observed_at = Column(Float, nullable=False)
    reason_code = Column(String(32))
    checkpointed = Column(Boolean, nullable=False, default=False)


class ResearchStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv("RESEARCH_DATA_DIR", "research_data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.engine = create_db_engine(f"sqlite:///{(self.root / 'research.db').as_posix()}")
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self):
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as db:
            if db.get(ResearchWorker, 1) is None:
                db.add(ResearchWorker(id=1, heartbeat=0))

    def close(self):
        self.engine.dispose()
