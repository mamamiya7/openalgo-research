"""Isolated Scanner Research metadata; sessions never escape their context."""

import os
from pathlib import Path

from sqlalchemy import Column, Float, Integer, String, Text
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
