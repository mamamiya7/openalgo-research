"""Isolated local preview, without loading OpenAlgo strategies or brokers.

uv run python tools/research_dev.py
Uses a disposable test identity on 127.0.0.1:5107, never production auth.
Owns a calculation worker unless --no-worker is specified.
A forced host/process-tree kill cannot run Python cleanup: wait for the worker's
120-second lease to expire before restarting this preview. Never clear a live lease.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / ".agent-native" / "dev" / "research"
DATA.mkdir(parents=True, exist_ok=True)
settings = ROOT / ".agent-native" / "dev" / "settings.json"
if settings.is_file():
    configuration = json.loads(settings.read_text(encoding="utf-8-sig"))
    if configuration.get("RESEARCH_PUBLIC_EVIDENCE_DIR"):
        os.environ["RESEARCH_PUBLIC_EVIDENCE_DIR"] = configuration["RESEARCH_PUBLIC_EVIDENCE_DIR"]
os.environ["RESEARCH_DATA_DIR"] = str(DATA)
os.environ["RESEARCH_REQUIRE_ISOLATED_ARCHIVE"] = "true"
os.environ["LOG_DIR"] = str(ROOT / ".agent-native" / "dev" / "log")
os.environ["LOG_FORMAT"] = "%(levelname)s %(name)s %(message)s"

import dotenv

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

from flask import Flask, jsonify, request, send_from_directory, session
from flask_wtf.csrf import CSRFProtect, generate_csrf

from blueprints.scanner_research import scanner_research_bp
from utils.logging import get_logger

logger = get_logger(__name__)
app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=secrets.token_hex(32),
    RESEARCH_DATA_DIR=str(DATA),
    MAX_CONTENT_LENGTH=8 * 1024 * 1024 + 65536,
    RESEARCH_PREVIEW=True,
)
CSRFProtect(app)
app.register_blueprint(scanner_research_bp)


@app.before_request
def isolated_identity():
    session["user"] = "isolated-fixture-preview"
    if (
        request.method == "POST"
        and request.path.endswith("/sources")
        and request.form.get("source") not in ("fixture", "public")
    ):
        return jsonify(
            message="This isolated preview permits synthetic fixtures and verified public evidence only"
        ), 400


@app.get("/auth/session-status")
def session_status():
    return jsonify(
        status="success",
        authenticated=True,
        logged_in=False,
        user="isolated-fixture-preview",
        broker=None,
    )


@app.get("/auth/csrf-token")
def csrf_token():
    return jsonify(csrf_token=generate_csrf())


@app.get("/settings/theme")
def theme():
    return jsonify(status="success", theme="light")


@app.get("/", defaults={"path": ""})
@app.get("/<path:path>")
def frontend(path):
    preview = ROOT / ".agent-native" / "dev" / "frontend"
    directory = preview if (preview / "index.html").is_file() else ROOT / "frontend" / "dist"
    if path and (directory / path).is_file():
        return send_from_directory(directory, path)
    return send_from_directory(directory, "index.html")


def stop_owned_worker(worker, stop_file, *, grace_seconds=10, kill_seconds=10):
    """Stop only this child's tree, including Windows venv interpreter children.

    Remember descendants before requesting graceful exit: a launcher can exit
    before its interpreter, making later parent-based discovery impossible.
    psutil Process objects retain creation identity for PID-reuse-safe signals.
    """
    descendants = {}

    def remember_children():
        if worker.poll() is None:
            try:
                for child in psutil.Process(worker.pid).children(recursive=True):
                    descendants[(child.pid, child.create_time())] = child
            except psutil.NoSuchProcess:
                pass

    try:
        remember_children()
        stop_file.write_text("stop", encoding="utf-8")
        try:
            worker.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            pass
    finally:
        try:
            # Catch children created while the worker was finishing, without
            # losing the identities remembered before a launcher disappeared.
            remember_children()
            for child in reversed(list(descendants.values())):
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(list(descendants.values()), timeout=kill_seconds)
            for child in alive:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(alive, timeout=kill_seconds)
            # Reap the actual Popen handle even when descendants exited first.
            if worker.poll() is None:
                worker.terminate()
            try:
                worker.wait(timeout=kill_seconds)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=kill_seconds)
            if alive:
                raise RuntimeError("An owned research worker descendant did not exit")
        finally:
            stop_file.unlink(missing_ok=True)


def shutdown_preview(worker, stop_file, store):
    """Database cleanup must survive process-wait and stop-file failures."""
    try:
        if worker is not None:
            stop_owned_worker(worker, stop_file)
    finally:
        store.close()


def ensure_worker_started(worker, *, timeout=1):
    """Surface immediate lease/import failures before starting the HTTP preview."""
    try:
        code = worker.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return
    raise RuntimeError(
        f"Research worker exited during startup (code {code}); inspect .agent-native/dev/worker.log. "
        "After a forced host shutdown, wait for the 120-second worker lease to expire before restarting."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-worker", action="store_true", help="Use a separately managed calculation worker"
    )
    args = parser.parse_args()
    logger.info(
        "Isolated research preview at http://127.0.0.1:5107/scanner-research; RESEARCH_DATA_DIR=%s",
        DATA,
    )
    stop_file = ROOT / ".agent-native" / "dev" / ("worker-stop-" + secrets.token_hex(8))
    worker = None
    try:
        with (ROOT / ".agent-native" / "dev" / "worker.log").open("a", encoding="utf-8") as output:
            if not args.no_worker:
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "services.scanner_research_worker",
                        "--stop-file",
                        str(stop_file),
                    ],
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                ensure_worker_started(worker)
            app.run(host="127.0.0.1", port=5107, use_reloader=False)
    finally:
        shutdown_preview(worker, stop_file, app.extensions["research_store"])
