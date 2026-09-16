"""Test a release ZIP in a fresh, private installation (Windows or Linux).

No existing account, configuration, database, cookies or broker credentials are
read. Prices are explicit test fixtures, not evidence of broker connectivity.
Run: python tools/research_install_smoke.py --archive RELEASE.zip --directory ABSOLUTE_EMPTY_PATH
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import zipfile
from datetime import UTC
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fresh_directory(path):
    """No guessed working folders, existing contents, links or filesystem roots."""
    path = Path(path)
    if not path.is_absolute() or path == Path(path.anchor):
        raise ValueError("Use an absolute, new or empty installation directory")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("The installation path must not contain links or junctions")
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("The installation directory must be new or empty")
    return path.resolve()


def safe_archive_name(name):
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or PureWindowsPath(name).drive
        or "\\" in name
        or any(
            part in (".", "..", "") or ":" in part or part.endswith((" ", "."))
            for part in name.split("/")
        )
    ):
        raise ValueError("Unsafe path in release archive")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if any(part.split(".")[0].upper() in reserved for part in path.parts):
        raise ValueError("Reserved path in release archive")
    return path


def validate_archive(bundle, *, expected_version=None):
    infos = bundle.infolist()
    if not infos or len(infos) > 50000 or sum(info.file_size for info in infos) > 2 * 1024**3:
        raise ValueError("Release archive exceeds validation limits")
    names = {}
    for info in infos:
        safe_archive_name(info.filename)
        mode = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or mode not in (0, stat.S_IFREG) or info.file_size > 128 * 1024**2:
            raise ValueError("Release archive must contain bounded regular files only")
        folded = info.filename.casefold()
        if folded in names:
            raise ValueError("Duplicate archive path")
        names[folded] = info.filename
    if (
        "release_manifest.json" not in names
        or bundle.getinfo("RELEASE_MANIFEST.json").file_size > 16 * 1024**2
    ):
        raise ValueError("Release manifest is missing or too large")
    manifest = json.loads(bundle.read("RELEASE_MANIFEST.json"))
    if manifest.get("format") != "openalgo-native-source-build-v1":
        raise ValueError("Unsupported release manifest")
    version = manifest.get("release_version")
    if not isinstance(version, str) or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.]+)?", version
    ):
        raise ValueError("Invalid release version")
    if expected_version is not None and version != expected_version:
        raise ValueError("Archive is not the expected release version")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(names.values()) - {"RELEASE_MANIFEST.json"}:
        raise ValueError("Archive content does not match the manifest inventory")
    for name, expected in files.items():
        for parent in PurePosixPath(name).parents:
            if str(parent).casefold() in names:
                raise ValueError("Archive file conflicts with a directory")
        info = bundle.getinfo(name)
        if not isinstance(expected, dict) or expected.get("bytes") != info.file_size:
            raise ValueError("Release size mismatch")
        with bundle.open(info) as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected.get("sha256"):
            raise ValueError("Release checksum mismatch")
    required = {
        "Setup.cmd",
        "setup-research.sh",
        "tools/research_install_smoke.py",
        "tools/research_desktop.py",
        "frontend/dist/index.html",
        "research/distribution.json",
        ".sample.env",
        "uv.lock",
        "pyproject.toml",
    }
    if (
        not required.issubset(files)
        or ".env" in files
        or any(name.startswith(("db/", "research_data/")) for name in files)
    ):
        raise ValueError("Release is incomplete or contains private runtime files")
    distribution = json.loads(bundle.read("research/distribution.json"))
    if distribution != manifest.get("distribution") or distribution.get("version") != version:
        raise ValueError("Distribution version does not match the release manifest")
    return manifest


def extract_archive(archive, directory, *, expected_version=None):
    directory = fresh_directory(directory)
    archive = Path(archive).resolve(strict=True)
    if not archive.is_file() or archive.is_relative_to(directory):
        raise ValueError("Archive must be a regular file outside the installation directory")
    archive_hash = digest(archive)
    sums = archive.parent / "SHA256SUMS"
    if sums.is_file():
        matching = [
            line.split()[0]
            for line in sums.read_text().splitlines()
            if len(line.split()) == 2 and line.split()[1].lstrip("*") == archive.name
        ]
        if matching != [archive_hash]:
            raise ValueError("Archive does not match SHA256SUMS")
    with zipfile.ZipFile(archive) as bundle:
        manifest = validate_archive(bundle, expected_version=expected_version)
        if archive.name != f"openalgo-research-{manifest['release_version']}.zip":
            raise ValueError("Archive filename and release version differ")
        # All validation finishes before creating or writing the destination.
        directory.mkdir(parents=True, exist_ok=True)
        for info in bundle.infolist():
            target = directory / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            if os.name == "posix":
                target.chmod(0o755 if info.filename.endswith(".sh") else 0o644)
    return manifest, archive_hash


def clean_environment(directory):
    # Keep only OS plumbing. No inherited broker, Python, database or package config.
    allowed = {
        "systemroot",
        "windir",
        "systemdrive",
        "comspec",
        "path",
        "pathext",
        "programfiles",
        "programfiles(x86)",
        "programdata",
        "processor_architecture",
        "number_of_processors",
        "os",
        "lang",
        "lc_all",
    }
    result = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    private = directory / "tmp/install-smoke"
    for name in ("home", "temp", "appdata", "localappdata"):
        (private / name).mkdir(parents=True, exist_ok=True)
    result.update(
        HOME=str(private / "home"),
        USERPROFILE=str(private / "home"),
        APPDATA=str(private / "appdata"),
        LOCALAPPDATA=str(private / "localappdata"),
        TEMP=str(private / "temp"),
        TMP=str(private / "temp"),
        TMPDIR=str(private / "temp"),
        XDG_CACHE_HOME=str(private / "home/.cache"),
        XDG_CONFIG_HOME=str(private / "home/.config"),
        PYTHONUTF8="1",
        PYTHONDONTWRITEBYTECODE="1",
        UV_NO_CONFIG="1",
        CI="1",
        OPENALGO_SETUP_NO_PAUSE="1",
        OPENBLAS_NUM_THREADS="2",
        NUMBA_NUM_THREADS="2",
        ORDER_UPDATES_ENABLED="FALSE",
        LOG_LEVEL="WARNING",
        LOG_TO_FILE="False",
    )
    return result


def free_ports():
    with contextlib.ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(3)]
        for connection in sockets:
            connection.bind(("127.0.0.1", 0))
        return [connection.getsockname()[1] for connection in sockets]


class OwnedCommand:
    """A bounded process group / Windows Job; no undrained pipes or broad PID search."""

    def __init__(self, command, directory, environment, log):
        from tools.research_desktop import WindowsJob

        self.job = None
        self.process = None
        self.log = None
        self.descendants = {}
        try:
            self.job = WindowsJob()
            self.log = Path(log).open("wb")
            options = (
                {"start_new_session": True}
                if os.name == "posix"
                else {
                    "creationflags": subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | 0x4
                }
            )
            self.process = subprocess.Popen(
                command,
                cwd=directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=self.log,
                stderr=self.log,
                **options,
            )
            self.job.assign(self.process)
            self.job.resume(self.process)
            self.remember_descendants()
        except BaseException:
            self.close()
            raise

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            self.remember_descendants()
            try:
                code = self.process.wait(timeout=min(0.2, max(0, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    raise
        if code:
            raise RuntimeError(
                f"Installation check failed (exit {code}); inspect {Path(self.log.name).name}"
            )

    @staticmethod
    def linux_identity(pid):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            return int(fields[1]), fields[19]  # Parent PID and kernel start time.
        except (OSError, IndexError, ValueError):
            return None

    def remember_descendants(self):
        if os.name != "posix" or self.process is None:
            return
        snapshot = {}
        for path in Path("/proc").iterdir():
            if path.name.isdecimal():
                identity = self.linux_identity(int(path.name))
                if identity is not None:
                    snapshot[int(path.name)] = identity
        parents = {self.process.pid}
        while parents:
            found = {pid for pid, identity in snapshot.items() if identity[0] in parents}
            for pid in found:
                self.descendants[pid] = snapshot[pid][1]
            parents = found
        self.descendants = {
            pid: started
            for pid, started in self.descendants.items()
            if pid in snapshot and snapshot[pid][1] == started
        }

    def signal_descendants(self, signum):
        for pid, started in self.descendants.items():
            identity = self.linux_identity(pid)
            if identity is not None and identity[1] == started:
                try:
                    os.kill(pid, signum)
                except ProcessLookupError:
                    pass

    def close(self):
        try:
            self.remember_descendants()
            if self.process is not None and self.process.poll() is None:
                if os.name == "posix":
                    self.signal_descendants(signal.SIGTERM)
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                else:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                if self.job is not None:
                    self.job.close()
                if self.process is not None:
                    if os.name == "posix":
                        self.signal_descendants(signal.SIGKILL)
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    elif self.process.poll() is None:
                        self.process.kill()
                    self.process.wait(timeout=10)
                    if os.name == "nt":
                        self.process._handle.Close()
            finally:
                if self.log is not None:
                    self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def bootstrap_command(directory, ports):
    arguments = [
        "--broker",
        "fyers",
        "--no-launch",
        "--port",
        str(ports[0]),
        "--websocket-port",
        str(ports[1]),
        "--zmq-port",
        str(ports[2]),
    ]
    if os.name == "nt":
        # cmd executes the actual public Setup.cmd. The generated directory may
        # contain spaces; argument escaping is delegated to Windows list2cmdline.
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            str(directory / "Setup.cmd"),
            *arguments,
        ]
    return ["bash", str(directory / "setup-research.sh"), *arguments]


def installed_python(directory):
    return directory / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def install(directory, environment, ports, name):
    with OwnedCommand(
        bootstrap_command(directory, ports),
        directory,
        environment,
        directory / f"tmp/install-smoke/{name}.log",
    ) as child:
        child.wait(1800)


def _check(value, message):
    if not value:
        raise RuntimeError(message)


def exercise(directory, ports):
    """Executed only by the newly installed interpreter, using its own packages."""
    import requests
    from dotenv import dotenv_values

    evidence = directory / "tmp/install-smoke"
    receipt_path = evidence / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    _check(receipt.get("state") == "installed", "Missing successful bootstrap receipt")
    environment = clean_environment(directory)
    config = dotenv_values(directory / ".env")
    _check(
        not config.get("BROKER_API_KEY") and not config.get("BROKER_API_SECRET"),
        "Smoke installation unexpectedly contains broker credentials",
    )
    for key in ("APP_KEY", "API_KEY_PEPPER", "FERNET_SALT"):
        _check(
            bool(config.get(key)) and "PLACEHOLDER" not in config[key],
            "Fresh setup did not create unique account secrets",
        )
    baseline_env = digest(directory / ".env")
    base = f"http://127.0.0.1:{ports[0]}"
    username = "smoke" + secrets.token_hex(4)
    password = "Fresh!" + secrets.token_urlsafe(18) + "Aa1"
    session = requests.Session()
    session.trust_env = False
    checks = receipt.setdefault("checks", {})
    saved = {}

    def get(path, *, json_response=True):
        with session.get(base + path, timeout=15) as response:
            _check(response.status_code == 200, f"GET {path} returned {response.status_code}")
            return response.json() if json_response else response.content

    def post(path, **kwargs):
        token = get("/auth/csrf-token")["csrf_token"]
        with session.post(
            base + path, headers={"X-CSRFToken": token}, timeout=30, **kwargs
        ) as response:
            _check(
                response.status_code in (200, 201, 202),
                f"POST {path} returned {response.status_code}",
            )
            return response.json()

    def login():
        response = post("/auth/login", data={"username": username, "password": password})
        _check(response.get("status") == "success", "Fresh account login failed")

    def completed(job):
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            value = get(f"/scanner-research/api/jobs/{job['id']}")
            if value["status"] == "completed":
                return value
            if value["status"] in ("failed", "cancelled", "interrupted"):
                # Job errors contain no credentials, but remain in private logs.
                raise RuntimeError(f"Controlled calculation failed: {value.get('error')}")
            time.sleep(0.3)
        raise RuntimeError("Controlled calculation exceeded five minutes")

    def launcher_ready(child):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            _check(child.process.poll() is None, "Desktop launcher exited during startup")
            # Launcher prints this only after HTTP token and worker lease readiness.
            text = Path(child.log.name).read_text(encoding="utf-8", errors="replace")
            if "OpenAlgo Research is ready:" in text:
                return
            time.sleep(0.2)
        raise RuntimeError("Desktop launcher did not become ready")

    def run_cycle(index):
        stop = evidence / f"cycle-{index}.stop"
        command = [
            str(installed_python(directory)),
            "tools/research_desktop.py",
            "--no-browser",
            "--stop-file",
            str(stop),
        ]
        with OwnedCommand(
            command, directory, environment, evidence / f"launch-{index}.log"
        ) as child:
            try:
                launcher_ready(child)
                if index == 1:
                    _check(
                        get("/auth/check-setup")["needs_setup"] is True,
                        "Installation is not a fresh account",
                    )
                    setup = get("/setup", json_response=False).decode()
                    assets = re.findall(r'src="(/assets/[^\"]+)"', setup)
                    _check('id="root"' in setup and bool(assets), "Built setup frontend missing")
                    _check(
                        bool(get(assets[0], json_response=False)), "Built JavaScript unavailable"
                    )
                    checks["built_setup_frontend"] = True
                    token = get("/auth/csrf-token")["csrf_token"]
                    with session.post(
                        base + "/setup",
                        data={
                            "username": username,
                            "email": username + "@example.invalid",
                            "password": password,
                        },
                        headers={"X-CSRFToken": token},
                        allow_redirects=False,
                        timeout=30,
                    ) as response:
                        _check(response.status_code == 302, "Fresh account setup failed")
                    login()
                    _check(
                        get("/scanner-research/api/health")["worker_state"] == "online",
                        "Worker is not online",
                    )
                    broker = get("/api/broker/credentials")["data"]
                    _check(
                        broker["current_broker"] == "fyers"
                        and broker["broker_api_key_raw_length"] == 0,
                        "Profile broker setup endpoint failed",
                    )
                    checks["fresh_account_login_and_broker_profile"] = True
                    seed_prices(directory, config)
                    source = post(
                        "/scanner-research/api/portfolio/inputs",
                        files={
                            "file": (
                                "controlled-signals.csv",
                                b"Date,Symbol\n2026-01-05,AAA\n",
                                "text/csv",
                            )
                        },
                    )
                    portfolio = {
                        "version": "research-portfolio-v1",
                        "name": "Release installation check",
                        "capital": 1000,
                        "engine": "vectorbt",
                        "strategies": [
                            {
                                "id": "a",
                                "name": "Target 10",
                                "type": "signals",
                                "source_id": source["id"],
                                "allocation_pct": 50,
                                "config": {
                                    "order_size_pct": 100,
                                    "hold_sessions": 1,
                                    "cost_bps": 0,
                                    "target_pct": 10,
                                },
                            },
                            {
                                "id": "b",
                                "name": "Target 30",
                                "type": "signals",
                                "source_id": source["id"],
                                "allocation_pct": 50,
                                "config": {
                                    "order_size_pct": 100,
                                    "hold_sessions": 1,
                                    "cost_bps": 0,
                                    "target_pct": 30,
                                },
                            },
                        ],
                    }
                    _check(
                        post("/scanner-research/api/portfolio/preflight", json=portfolio)[
                            "interval"
                        ]
                        == "D",
                        "Daily CSV did not select daily candles",
                    )
                    fixed = completed(
                        post(
                            "/scanner-research/api/portfolio/jobs",
                            json={"portfolio": portfolio, "request_id": "installation-fixed"},
                        )
                    )
                    _check(
                        fixed["result"]["execution"]["engine"] == "vectorbt",
                        "Expected real VectorBT execution",
                    )
                    _check(
                        fixed["result"]["summary"]["final_equity"] == 1075
                        and fixed["result"]["summary"]["closed_trades"] == 2,
                        "Controlled portfolio calculation differs from expected trades",
                    )
                    fixed_export = get(
                        f"/scanner-research/api/jobs/{fixed['id']}/export", json_response=False
                    )
                    provenance = json.loads(fixed_export)["inputs"]["snapshot"]["provenance"]
                    _check(
                        provenance.get("download_brokers") == [],
                        "Controlled archive fixture unexpectedly downloaded broker prices",
                    )
                    saved[fixed["id"]] = fixed_export
                    optimization = copy.deepcopy(portfolio)
                    optimization["strategies"][1]["search"] = {
                        "target_pct": {"min": 10, "max": 30, "step": 10}
                    }
                    optimization["optimization"] = {
                        "sampler": "tpe",
                        "trials": 3,
                        "objective": "balanced",
                        "seed": 0,
                    }
                    optimized = completed(
                        post(
                            "/scanner-research/api/portfolio/jobs",
                            json={"portfolio": optimization, "request_id": "installation-optuna"},
                        )
                    )
                    _check(
                        optimized["result"]["experiment"]["optimizer"]["sampler"] == "TPESampler",
                        "Expected real Optuna TPE execution",
                    )
                    _check(
                        optimized["result"]["experiment"]["counts"]["proposed"] == 3,
                        "Optuna did not finish its three requested proposals",
                    )
                    saved[optimized["id"]] = get(
                        f"/scanner-research/api/jobs/{optimized['id']}/export", json_response=False
                    )
                    rerun = completed(
                        post(
                            f"/scanner-research/api/portfolio/jobs/{optimized['id']}/rerun",
                            json={"request_id": "installation-replay"},
                        )
                    )
                    for key in ("summary", "ledger", "equity_curve", "per_strategy"):
                        _check(
                            rerun["result"][key] == optimized["result"][key],
                            "Saved selected setup did not replay exactly",
                        )
                    checks.update(
                        daily_csv_archive_reuse=True,
                        vectorbt_backtest=True,
                        optuna_three_trials=True,
                        exact_selected_replay=True,
                    )
                    receipt["jobs"] = {
                        "fixed": fixed["id"],
                        "optimization": optimized["id"],
                        "replay": rerun["id"],
                    }
                    receipt["export_sha256"] = {
                        job: hashlib.sha256(raw).hexdigest() for job, raw in saved.items()
                    }
                else:
                    _check(
                        get("/auth/check-setup")["needs_setup"] is False,
                        "Account was lost across reinstall/restart",
                    )
                    session.cookies.clear()
                    login()
                    for job, raw in saved.items():
                        _check(
                            get(f"/scanner-research/api/jobs/{job}/export", json_response=False)
                            == raw,
                            "Saved export changed across reinstall/restart",
                        )
                    _check(
                        get("/scanner-research/api/health")["worker_state"] == "online",
                        "Worker did not restart",
                    )
                    checks["reinstall_restart_preserves_account_exports"] = True
            finally:
                stop.touch()
                child.wait(55)

    try:
        run_cycle(1)
        _check(
            digest(directory / ".env") == baseline_env, "First run changed installer-created .env"
        )
        install(directory, environment, ports, "reinstall")
        _check(digest(directory / ".env") == baseline_env, "Reinstall changed existing .env")
        run_cycle(2)
        _check(digest(directory / ".env") == baseline_env, "Restart changed existing .env")
        from tools.research_desktop import InstanceLock, check_ports

        check_ports(list(zip(("website", "WebSocket", "market-data"), ports, strict=True)))
        with InstanceLock(directory / "tmp/research-desktop.lock"):
            pass
        checks["services_release_ports_and_instance_lock"] = True
        receipt["state"] = "passed"
    except BaseException as error:
        receipt["state"] = "failed"
        receipt["error"] = str(error)
        raise
    finally:
        session.close()
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"state": receipt["state"], "checks": checks}), flush=True)


def seed_prices(directory, config):
    """Seed only the fresh installation's own configured native Historify archive."""
    from datetime import datetime, timezone

    os.environ.update({key: value for key, value in config.items() if value is not None})
    from database.historify_db import get_db_path
    from services.research_historify import native_historify_write

    archive = Path(get_db_path()).resolve()
    _check(
        archive.is_relative_to(directory) and archive != directory,
        "Historify path escaped the fresh installation",
    )
    rows = []
    for date, prices in (
        ("2026-01-05", (100, 100, 100, 100)),
        ("2026-01-06", (100, 112, 99, 108)),
        ("2026-01-07", (108, 109, 104, 105)),
        ("2026-01-08", (105, 105, 105, 105)),
    ):
        rows.append(
            {
                "timestamp": int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp()),
                **dict(zip(("open", "high", "low", "close"), prices, strict=True)),
                "volume": 1000,
            }
        )
    _check(
        native_historify_write("AAA", rows, archive, interval="D") == 4,
        "Native Historify fixture write failed",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--exercise", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.exercise:
        directory = args.directory.resolve(strict=True)
        _check(directory == ROOT, "Exercise must use the freshly extracted script")
        receipt = json.loads((directory / "tmp/install-smoke/receipt.json").read_text())
        _check(receipt["directory"] == str(directory), "Smoke directory identity differs")
        os.chdir(directory)
        exercise(directory, receipt["ports"])
        return 0
    if args.archive is None:
        parser.error("--archive is required")
    expected = json.loads((ROOT / "research/distribution.json").read_text())["version"]
    manifest, archive_hash = extract_archive(
        args.archive, args.directory, expected_version=expected
    )
    directory = args.directory.resolve()
    environment = clean_environment(directory)
    ports = free_ports()
    receipt_path = directory / "tmp/install-smoke/receipt.json"
    receipt = {
        "state": "installing",
        "version": manifest["release_version"],
        "archive_sha256": archive_hash,
        "directory": str(directory),
        "ports": ports,
        "basis": "Fresh ZIP, private managed runtime, new account, controlled native archive candles; no broker download validation.",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("Testing the exact release in a fresh private installation...", flush=True)
    try:
        install(directory, environment, ports, "bootstrap")
        receipt["state"] = "installed"
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        with OwnedCommand(
            [
                str(installed_python(directory)),
                "tools/research_install_smoke.py",
                "--directory",
                str(directory),
                "--exercise",
            ],
            directory,
            environment,
            directory / "tmp/install-smoke/workflow.log",
        ) as child:
            child.wait(1800)
        finished = json.loads(receipt_path.read_text())
        _check(finished["state"] == "passed", "Fresh installation acceptance did not pass")
    except BaseException as error:
        current = json.loads(receipt_path.read_text())
        if current["state"] != "failed":
            current.update(state="failed", error=str(error))
            receipt_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        raise
    print(f"Fresh installation passed. Receipt: {receipt_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
