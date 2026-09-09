"""Isolated public-price/generated-membership external-worker capacity receipt.

No broker calls, credentials, strategies or existing research stores are used.
The small HTTP host mounts the real research blueprint with a disposable local
identity; it is not a production-authentication or integrated-startup benchmark.
"""

import argparse
import gc
import hashlib
import http.cookiejar
import json
import math
import os
import platform
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure(directory):
    os.environ["RESEARCH_DATA_DIR"] = str(directory / "research")
    os.environ["LOG_DIR"] = str(directory / "logs")
    os.environ["HISTORIFY_DATABASE_PATH"] = str(directory / "unused-historify.duckdb")
    os.environ["RESEARCH_QUOTA_MB"] = "4096"
    os.environ["LOG_FORMAT"] = "%(levelname)s %(name)s %(message)s"
    # Prevent development helper imports from reading any project .env.
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False
    dotenv.main.load_dotenv = dotenv.load_dotenv


def serve(directory, port):
    configure(directory)
    from flask import Flask, jsonify, session
    from flask_wtf.csrf import CSRFProtect, generate_csrf
    from werkzeug.serving import make_server

    from blueprints.scanner_research import scanner_research_bp

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secrets.token_hex(32), RESEARCH_DATA_DIR=str(directory / "research")
    )
    CSRFProtect(app)
    app.register_blueprint(scanner_research_bp)

    @app.before_request
    def isolated_identity():
        session["user"] = "isolated-capacity-benchmark"

    @app.get("/benchmark/csrf")
    def csrf():
        return jsonify(csrf_token=generate_csrf())

    server = make_server("127.0.0.1", port, app, threaded=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        app.extensions["research_store"].close()


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024**2), b""):
            result.update(part)
    return result.hexdigest()


def latency_receipt(samples):
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "max_seconds": max(ordered, default=0),
        "p95_seconds": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)] if ordered else 0,
        "mean_seconds": sum(ordered) / len(ordered) if ordered else 0,
    }


def benchmark(args):
    import psutil

    if not 30 <= args.configurations <= 3000 or not 30 <= args.timeout <= 3600:
        raise ValueError("Use 30–3000 configurations and a 30–3600 second timeout")
    output = (
        ROOT
        / ".agent-native"
        / "benchmarks"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4))
    )
    output.mkdir(parents=True)
    configure(output)
    from database.research_db import ResearchExperiment, ResearchSource, ResearchStore
    from research.engine import POLICY_VERSION, config_defaults
    from services.scanner_research_service import encoded, read_artifact, save_artifact

    code_paths = [
        "research/engine.py",
        "research/experiments.py",
        "services/scanner_research_worker.py",
        "services/scanner_research_service.py",
        "research/evidence_import.py",
    ]
    source_hashes = {path: digest(ROOT / path) for path in code_paths}
    signal_path = ROOT / ".agent-native" / "public-evidence" / "scale-signals.json"
    snapshot_path = ROOT / ".agent-native" / "public-evidence" / "scale-snapshot.json"
    signals = json.loads(signal_path.read_bytes())
    snapshot = json.loads(snapshot_path.read_bytes())
    if len(signals) != 18310 or snapshot["provenance"].get("synthetic"):
        raise ValueError(
            "Benchmark requires 18310 generated memberships and non-synthetic prepared prices"
        )
    metadata = {
        "signals": len(signals),
        "symbols": len({s["symbol"] for s in signals}),
        "sessions": len(snapshot["sessions"]),
        "bars": sum(len(v) for v in snapshot["bars"].values()),
        "signals_sha256": digest(signal_path),
        "snapshot_sha256": digest(snapshot_path),
        "provider": snapshot["provenance"].get("provider"),
        "coverage_status": snapshot["coverage"]["status"],
        "memberships": "Generated benchmark memberships; not a historical scanner selection record",
    }
    store = ResearchStore(output / "research")
    store.initialize()
    source_id = secrets.token_hex(16)
    artifact = save_artifact(
        store,
        {
            "signals": signals,
            "snapshot": snapshot,
            "receipt": {
                "input_rows": len(signals),
                "signal_count": len(signals),
                "symbol_count": metadata["symbols"],
            },
            "benchmark_memberships": metadata["memberships"],
        },
    )
    with store.sessions.begin() as db:
        db.add(
            ResearchSource(
                id=source_id,
                owner="isolated-capacity-benchmark",
                artifact=artifact,
                created_at=time.time(),
            )
        )
    del signals, snapshot
    gc.collect()
    specification = {
        "mode": "exhaustive" if args.configurations == 3000 else "full",
        "budget": args.configurations,
        "axes": {
            "target_pct": {"min": 5, "max": 24, "step": 1},
            "stop_pct": {"min": 2, "max": 11, "step": 1},
            "hold_sessions": {"min": 5, "max": 75, "step": 5},
            "trailing_pct": {"min": 0, "max": 0, "step": 1},
        },
        "mode_strategies": [["Bypass"]],
        "include_trailing_off": True,
        "rank_by": "balance",
    }
    base = f"http://127.0.0.1:{args.port}"
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    csrf_token = None
    latencies = {"health": [], "jobs": []}
    failed_requests = []
    process_metrics = {}
    observed_descendants = {}
    processes, worker = [], None
    stop_file = output / "worker.stop"
    start, deadline = time.perf_counter(), time.monotonic() + args.timeout
    report = {
        "output": str(output),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_logical": psutil.cpu_count(),
        "cpu_physical": psutil.cpu_count(logical=False),
        "system_memory_bytes": psutil.virtual_memory().total,
        "input": metadata,
        "configurations": args.configurations,
        "policy_version": POLICY_VERSION,
        "source_hashes": source_hashes,
        "specification": specification,
        "limits": [
            "Public-price inputs with generated signal memberships; no inference about scanner profitability.",
            "Isolated Windows threaded Flask blueprint host; not integrated OpenAlgo startup, eventlet or production authentication.",
            "Graceful supervisor stop-file interruption; hard-crash lease expiry and host restart are separately tested.",
            "Sampled RSS with operating-system peak working set when available; filesystem cache is not included.",
        ],
    }

    def request(path, body=None, bucket=None):
        before = time.perf_counter()
        data = None if body is None else encoded(body)
        headers = (
            {}
            if body is None
            else {"Content-Type": "application/json", "X-CSRFToken": csrf_token or ""}
        )
        try:
            with opener.open(
                urllib.request.Request(base + path, data=data, headers=headers), timeout=5
            ) as response:
                value = json.loads(response.read(2 * 1024**2))
        except (OSError, urllib.error.HTTPError) as error:
            if bucket:
                failed_requests.append(
                    {
                        "endpoint": bucket,
                        "error": str(error),
                        "elapsed": time.perf_counter() - start,
                    }
                )
            raise
        finally:
            if bucket:
                latencies[bucket].append(time.perf_counter() - before)
        return value

    def sample():
        for label, process in [
            ("controller", psutil.Process()),
            *[
                (name, psutil.Process(child.pid))
                for name, child in processes
                if child.poll() is None
            ],
        ]:
            try:
                tree = (
                    [process, *process.children(recursive=True)]
                    if label != "controller"
                    else [process]
                )
                for child in tree[1:]:
                    observed_descendants[(label, child.pid)] = child.create_time()
                memories = [child.memory_info() for child in tree]
                metrics = process_metrics.setdefault(
                    label, {"peak_rss_bytes": 0, "peak_working_set_bytes": 0, "cpu_seconds": 0}
                )
                metrics["peak_rss_bytes"] = max(
                    metrics["peak_rss_bytes"], sum(memory.rss for memory in memories)
                )
                metrics["peak_working_set_bytes"] = max(
                    metrics["peak_working_set_bytes"],
                    sum(getattr(memory, "peak_wset", memory.rss) for memory in memories),
                )
                cpus = [child.cpu_times() for child in tree]
                metrics["cpu_seconds"] = max(
                    metrics["cpu_seconds"], sum(cpu.user + cpu.system for cpu in cpus)
                )
            except psutil.Error:
                pass

    def spawn(command, name, stack):
        log = stack.enter_context((output / f"{name}.log").open("wb"))
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=os.environ.copy(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        processes.append((name, process))
        return process

    def spawn_worker(name, stack):
        stop_file.unlink(missing_ok=True)
        return spawn(
            [
                sys.executable,
                "-m",
                "services.scanner_research_worker",
                "--once",
                "--stop-file",
                str(stop_file),
            ],
            name,
            stack,
        )

    try:
        with ExitStack() as stack:
            try:
                server = spawn(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--serve",
                        "--data-root",
                        str(output),
                        "--port",
                        str(args.port),
                    ],
                    "http",
                    stack,
                )
                for _ in range(100):
                    if server.poll() is not None:
                        raise RuntimeError("Isolated HTTP server failed; inspect its log")
                    try:
                        csrf_token = request("/benchmark/csrf")["csrf_token"]
                        break
                    except OSError:
                        time.sleep(0.1)
                if csrf_token is None:
                    raise TimeoutError("Isolated HTTP startup timed out")
                submitted_at = time.perf_counter()
                job = request(
                    "/scanner-research/api/jobs",
                    {
                        "source_id": source_id,
                        "config": config_defaults(),
                        "kind": "optimize",
                        "specification": specification,
                        "request_id": "benchmark-" + secrets.token_hex(12),
                    },
                )
                report["http_submission_seconds"] = time.perf_counter() - submitted_at
                job_id = report["job_id"] = job["id"]
                calculation_start = time.perf_counter()
                worker = spawn_worker("worker-initial", stack)
                interrupted, resumed = False, False
                checkpoint_rows = []
                next_log = time.monotonic()
                while time.monotonic() < deadline:
                    request("/scanner-research/api/health", bucket="health")
                    jobs = request("/scanner-research/api/jobs", bucket="jobs")
                    current = next(row for row in jobs if row["id"] == job_id)
                    counts = current.get("counts") or {}
                    completed = counts.get("completed", 0)
                    sample()
                    if not interrupted and completed >= min(25, args.configurations - 5):
                        report["interrupt_requested_at_seconds"] = (
                            time.perf_counter() - calculation_start
                        )
                        stop_file.write_text("benchmark supervised interruption", encoding="utf-8")
                        interrupted = True
                    if interrupted and not resumed and worker.poll() is not None:
                        if current["status"] != "interrupted":
                            raise RuntimeError(
                                f"Expected interrupted job, observed {current['status']}: {current.get('error')}"
                            )
                        with store.sessions() as db:
                            checkpoint_id = db.get(ResearchExperiment, job_id).checkpoint
                        checkpoint = read_artifact(store, checkpoint_id)
                        checkpoint_rows = checkpoint["state"]["rows"]
                        report["resume_checkpoint_rows"] = len(checkpoint_rows)
                        report["resume_checkpoint_id"] = checkpoint_id
                        if {path: digest(ROOT / path) for path in code_paths} != source_hashes:
                            raise RuntimeError(
                                "Evaluator/search/worker source changed before restart"
                            )
                        resumed_job = request(f"/scanner-research/api/jobs/{job_id}/resume", {})
                        if resumed_job["id"] != job_id or resumed_job["status"] != "queued":
                            raise RuntimeError("Resume did not preserve the same persisted job")
                        worker = spawn_worker("worker-resumed", stack)
                        resumed = True
                        report["resume_at_seconds"] = time.perf_counter() - calculation_start
                    if current["status"] == "completed":
                        if not resumed:
                            raise RuntimeError("Search completed without testing resume")
                        report["worker_journey_seconds"] = time.perf_counter() - calculation_start
                        worker.wait(timeout=15)
                        report["result_artifact"] = current["evidence_id"]
                        bundle = read_artifact(store, current["evidence_id"])
                        experiment = bundle["result"]["experiment"]
                        final_rows = {row["grid_index"]: row for row in experiment["rows"]}
                        if len(final_rows) != args.configurations or any(
                            final_rows.get(row["grid_index"]) != row for row in checkpoint_rows
                        ):
                            raise RuntimeError(
                                "Resumed results do not preserve the exact checkpoint prefix or expected count"
                            )
                        report["exact_checkpoint_prefix_preserved"] = True
                        report["counts"] = experiment["counts"]
                        report["result_decoded_bytes"] = len(encoded(bundle))
                        report["result_compressed_bytes"] = (
                            (store.root / "artifacts" / f"{current['evidence_id']}.json.gz")
                            .stat()
                            .st_size
                        )
                        report["recommendation_id"] = experiment["recommendation_id"]
                        export_start, exported_hash, export_bytes = (
                            time.perf_counter(),
                            hashlib.sha256(),
                            0,
                        )
                        with (
                            opener.open(
                                base + f"/scanner-research/api/jobs/{job_id}/export", timeout=30
                            ) as response,
                            (output / "export.json").open("wb") as exported,
                        ):
                            expected = response.headers["X-Evidence-SHA256"]
                            for chunk in iter(lambda: response.read(1024**2), b""):
                                exported_hash.update(chunk)
                                export_bytes += len(chunk)
                                exported.write(chunk)
                        if exported_hash.hexdigest() != expected:
                            raise RuntimeError("HTTP exact evidence export hash mismatch")
                        report["export"] = {
                            "bytes": export_bytes,
                            "sha256": expected,
                            "seconds": time.perf_counter() - export_start,
                        }
                        sample()
                        report["status"] = "completed"
                        break
                    if current["status"] in ("failed", "cancelled"):
                        raise RuntimeError(f"Worker {current['status']}: {current.get('error')}")
                    if time.monotonic() >= next_log:
                        print(
                            json.dumps(
                                {
                                    "event": "progress",
                                    "output": str(output),
                                    "completed": completed,
                                    "total": args.configurations,
                                    "status": current["status"],
                                    "elapsed_seconds": round(
                                        time.perf_counter() - calculation_start, 2
                                    ),
                                }
                            ),
                            flush=True,
                        )
                        next_log = time.monotonic() + 20
                    time.sleep(0.5)
                else:
                    raise TimeoutError("Bounded benchmark deadline exceeded")
            finally:
                stop_file.write_text("benchmark cleanup", encoding="utf-8")
                for name, process in reversed(processes):
                    if process.poll() is None:
                        if name != "http":
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.terminate()
                        else:
                            try:
                                for child in psutil.Process(process.pid).children(recursive=True):
                                    observed_descendants[(name, child.pid)] = child.create_time()
                                    child.terminate()
                                    try:
                                        child.wait(timeout=10)
                                    except psutil.TimeoutExpired:
                                        child.kill()
                                        child.wait(timeout=10)
                            except psutil.NoSuchProcess:
                                pass
                            process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                for (_, pid), created in observed_descendants.items():
                    try:
                        child = psutil.Process(pid)
                        if child.create_time() == created:
                            child.terminate()
                            try:
                                child.wait(timeout=10)
                            except psutil.TimeoutExpired:
                                child.kill()
                                child.wait(timeout=10)
                    except psutil.NoSuchProcess:
                        pass
                report["children_reaped"] = all(
                    process.poll() is not None for _, process in processes
                )
    except Exception as error:
        report["status"], report["error"] = "failed", str(error)
    finally:
        store.close()
        report["total_seconds"] = time.perf_counter() - start
        report["http_latency"] = {key: latency_receipt(values) for key, values in latencies.items()}
        report["failed_http_requests"] = failed_requests
        report["processes"] = process_metrics
        report["research_storage_bytes"] = sum(
            path.stat().st_size for path in (output / "research").rglob("*") if path.is_file()
        )
        report["research_artifact_files"] = len(
            list((output / "research" / "artifacts").glob("*.json*"))
        )
        report["source_hashes_after"] = {path: digest(ROOT / path) for path in code_paths}
        (output / "receipt.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configurations", type=int, default=3000)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--port", type=int, default=5119)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--data-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        if args.data_root is None:
            parser.error("--serve requires isolated --data-root")
        serve(args.data_root, args.port)
        return 0
    return benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
