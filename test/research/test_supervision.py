# ruff: noqa: F811 -- pytest fixtures imported for injection
"""Exercise the real continuous worker CLI with isolated files and no broker."""

import os
import subprocess
import sys
import time

import psutil
from test_jobs import app, client, source, submit  # noqa: F401

from database.research_db import ResearchWorker


def wait_until(predicate, process, log):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if predicate():
            return
        assert process.poll() is None, log.read_text()
        time.sleep(0.05)
    raise AssertionError("Worker did not reach expected state: " + log.read_text())


def test_continuous_cli_health_graceful_release_and_restart(app, client, tmp_path):
    source_id = source(client)
    store = app.extensions["research_store"]
    stop = tmp_path / "supervisor.stop"
    log = tmp_path / "supervisor.log"
    environment = {**os.environ, "RESEARCH_DATA_DIR": str(store.root)}
    command = [sys.executable, "-m", "services.scanner_research_worker", "--stop-file", str(stop)]

    def health():
        return client.get("/scanner-research/api/health").json["worker_state"]

    assert health() == "offline"
    previous_token = None
    for attempt in range(2):
        stop.unlink(missing_ok=True)
        job_id = submit(client, source_id) if attempt else None
        with log.open("w") as output:
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                wait_until(lambda: health() == "online", process, log)
                with store.sessions() as db:
                    token = db.get(ResearchWorker, 1).token
                assert token and token != previous_token
                previous_token = token
                if job_id:
                    wait_until(
                        lambda job_id=job_id: (
                            client.get(f"/scanner-research/api/jobs/{job_id}").json["status"]
                            == "completed"
                        ),
                        process,
                        log,
                    )
                stop.touch()
                assert process.wait(timeout=15) == 0, log.read_text()
            finally:
                stop.touch()
                if process.poll() is None:
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        # Windows venv launchers can own an interpreter child.
                        descendants = psutil.Process(process.pid).children(recursive=True)
                        for child in descendants:
                            try:
                                child.kill()
                            except psutil.NoSuchProcess:
                                pass
                        process.kill()
                        _, alive = psutil.wait_procs(descendants, timeout=10)
                        assert not alive
                process.wait(timeout=10)
        assert health() == "offline"
        with store.sessions() as db:
            lease = db.get(ResearchWorker, 1)
            assert lease.token is None and lease.heartbeat == 0


def test_cli_startup_failure_does_not_publish_healthy_lease(app, client, tmp_path):
    # A path that cannot be initialized must fail before claiming a worker lease.
    source(client)
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("isolated startup failure")
    with (tmp_path / "failed-startup.log").open("w") as output:
        process = subprocess.run(
            [sys.executable, "-m", "services.scanner_research_worker", "--once"],
            env={**os.environ, "RESEARCH_DATA_DIR": str(invalid)},
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    assert process.returncode != 0
    assert client.get("/scanner-research/api/health").json["worker_state"] == "offline"
