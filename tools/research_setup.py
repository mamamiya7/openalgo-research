"""Install the locked Research desktop runtime without replacing private data.

Setup.cmd / setup-research.sh supply verified uv and managed Python 3.12 first.
This module is stdlib-only until dependency installation has completed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MIGRATIONS = (
    "upgrade/migrate_agent.py",
    "upgrade/migrate_agent_voice.py",
    "upgrade/migrate_agent_voice_phrase_removal.py",
    "upgrade/migrate_research_nse_calendar.py",
    "upgrade/migrate_scanner_research.py",
)
RELEASE_PAGE = "https://github.com/mamamiya7/openalgo-research/releases"


class SetupError(RuntimeError):
    """An actionable setup failure; never includes configuration values."""


def env_values(text: str) -> dict[str, str]:
    """Read simple scalar settings for preflight, without applying them to this process."""
    result = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        value = match[2].strip()
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            value = value[1:end] if end >= 1 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()
        result[match[1]] = value
    return result


def choose_broker(sample: str, broker: str | None, *, prompt=input) -> str:
    brokers = env_values(sample).get("VALID_BROKERS", "").split(",")
    brokers = sorted({value.strip().lower() for value in brokers if value.strip()})
    if not brokers:
        raise SetupError("The release has no supported broker list. Download the release again.")
    if broker is not None:
        value = broker.strip().lower()
        if value not in brokers:
            raise SetupError(f"Unknown broker. Choose one of: {', '.join(brokers)}")
        return value
    print("\nWhich broker will provide your prices? You can change this later in Profile.")
    print("Choose a broker name (no password or API key is needed here):")
    print(", ".join(brokers))
    while True:
        try:
            value = prompt("Broker: ").strip().lower()
        except (EOFError, KeyboardInterrupt) as exc:
            raise SetupError(
                "Choose your broker when running Setup, or pass --broker BROKER_NAME."
            ) from exc
        if value in brokers:
            return value
        matches = [name for name in brokers if value and value in name]
        if len(matches) == 1:
            print(f"Selected {matches[0]}.")
            return matches[0]
        print("Choose a listed name" + (f": {', '.join(matches)}" if matches else "."))


def installation_ports(
    root: Path, *, port=None, websocket_port=None, zmq_port=None
) -> dict[str, int]:
    """Resolve fresh defaults, admitting overrides only when existing values agree."""
    path = root / ".env"
    exists = path.exists()
    config = env_values(path.read_text(encoding="utf-8")) if exists else {}
    requested = {
        "FLASK_PORT": (port, 5000),
        "WEBSOCKET_PORT": (websocket_port, 8765),
        "ZMQ_PORT": (zmq_port, 5555),
    }
    ports = {}
    for key, (override, default) in requested.items():
        try:
            saved = int(config.get(key, str(default)))
            value = saved if override is None else int(override)
        except (TypeError, ValueError) as exc:
            raise SetupError(
                f"{key} must be a valid port number. Your .env was not changed."
            ) from exc
        if not 1 <= value <= 65535:
            raise SetupError(f"{key} must be between 1 and 65535.")
        if exists and override is not None and value != saved:
            raise SetupError(
                f"{key} conflicts with your existing .env. Port overrides apply only to a fresh setup; your configuration was not changed."
            )
        ports[key] = value
    if len(set(ports.values())) != len(ports):
        raise SetupError("The app, WebSocket and ZeroMQ ports must be different.")
    return ports


def ensure_env(
    root: Path, broker: str | None, *, port=None, websocket_port=None, zmq_port=None, prompt=input
) -> bool:
    """Create once with O_EXCL; never rewrite even one byte of an existing .env."""
    ports = installation_ports(root, port=port, websocket_port=websocket_port, zmq_port=zmq_port)
    target = root / ".env"
    if target.exists():
        print("Keeping your existing .env and data paths.")
        return False
    if (
        any((root / "db").glob("*.db"))
        or any((root / "db").glob("*.duckdb"))
        or (root / "research_data/research.db").exists()
    ):
        raise SetupError(
            "Saved data exists but .env is missing. Restore its original .env backup before running Setup; do not generate new account encryption keys."
        )
    sample = (root / ".sample.env").read_text(encoding="utf-8")
    broker = choose_broker(sample, broker, prompt=prompt)
    replacements = {
        "APP_KEY": secrets.token_hex(32),
        "API_KEY_PEPPER": secrets.token_hex(32),
        "FERNET_SALT": secrets.token_hex(32),
        "BROKER_API_KEY": "",
        "BROKER_API_SECRET": "",
        "BROKER_API_KEY_MARKET": "",
        "BROKER_API_SECRET_MARKET": "",
        "REDIRECT_URL": f"http://127.0.0.1:{ports['FLASK_PORT']}/{broker}/callback",
        "FLASK_HOST_IP": "127.0.0.1",
        "FLASK_PORT": str(ports["FLASK_PORT"]),
        "FLASK_DEBUG": "False",
        "HOST_SERVER": f"http://127.0.0.1:{ports['FLASK_PORT']}",
        "WEBSOCKET_HOST": "127.0.0.1",
        "WEBSOCKET_PORT": str(ports["WEBSOCKET_PORT"]),
        "WEBSOCKET_URL": f"ws://127.0.0.1:{ports['WEBSOCKET_PORT']}",
        "ZMQ_PORT": str(ports["ZMQ_PORT"]),
        "NGROK_ALLOW": "FALSE",
        "MCP_HTTP_ENABLED": "False",
    }
    for key, value in replacements.items():
        sample, count = re.subn(rf"(?m)^\s*{re.escape(key)}\s*=.*$", f"{key} = '{value}'", sample)
        if not count:
            sample += f"\n{key} = '{value}'\n"
    # Native first-run validation canonicalizes a valid salt to the line directly
    # after its pepper. Write that form now so startup need not rewrite .env.
    # This is fresh configuration only; existing files returned untouched above.
    sample = re.sub(r"(?m)^[ \t]*FERNET_SALT[ \t]*=.*(?:\n|$)", "", sample)
    sample = re.sub(
        r"(?m)^([ \t]*API_KEY_PEPPER[ \t]*=.*)$",
        lambda match: f"{match[1]}\nFERNET_SALT = '{replacements['FERNET_SALT']}'",
        sample,
    )
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("Keeping the .env created by another setup.")
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(sample)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print("Created private local settings and unique account encryption keys.")
    return True


class _Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.paths.append(values["src"])
        elif (
            tag == "link"
            and values.get("rel") in ("stylesheet", "modulepreload")
            and values.get("href")
        ):
            self.paths.append(values["href"])


def verify_frontend(root: Path) -> None:
    dist = (root / "frontend/dist").resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise SetupError(
            f"The built interface is missing. Get the openalgo-research ZIP from {RELEASE_PAGE}, not Source code. Developers: run npm ci, then npm run build inside frontend."
        )
    parser = _Assets()
    parser.feed(index.read_text(encoding="utf-8"))
    if not any(urlsplit(path).path.endswith(".js") for path in parser.paths):
        raise SetupError(
            "The interface has no built JavaScript. Extract the complete release ZIP again."
        )
    for name in parser.paths:
        url = urlsplit(name)
        asset = (dist / unquote(url.path).lstrip("/")).resolve()
        if url.scheme or url.netloc or not asset.is_relative_to(dist) or not asset.is_file():
            raise SetupError(
                "The built interface is incomplete. Extract the complete release ZIP again."
            )


def verify_release_files(root: Path) -> None:
    manifest = root / "RELEASE_MANIFEST.json"
    if not manifest.is_file():
        return  # A developer checkout has no release receipt; compatibility still runs.
    with manifest.open("r", encoding="utf-8") as handle:
        record = json.load(handle)
    files = record.get("files")
    if not isinstance(files, dict) or not files:
        raise SetupError("Invalid release manifest. Extract the release ZIP again.")
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise SetupError("A release file is missing. Extract the complete ZIP again.")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != expected.get("sha256"):
            raise SetupError(
                f"A release file was changed: {name}. Restore the release copy before running Setup; your .env and saved data are separate."
            )


def clean_environment(root: Path) -> dict[str, str]:
    """Do not inherit another OpenAlgo instance's broker/database environment."""
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LC_ALL",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(
        UV_PYTHON_INSTALL_DIR=str(root / ".research-runtime/python"),
        UV_CACHE_DIR=str(root / ".research-runtime/cache"),
        UV_PROJECT_ENVIRONMENT=str(root / ".venv"),
        UV_NO_PROGRESS="1",
        UV_LINK_MODE="copy",
        PYTHONUNBUFFERED="1",
        PYTHONUTF8="1",
    )
    return environment


def _stop(child: subprocess.Popen, log) -> None:
    if child.poll() is None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    stdout=log,
                    stderr=log,
                    check=False,
                    timeout=30,
                )
            else:
                os.killpg(child.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            if child.poll() is None:
                child.kill()
    child.wait(timeout=30)


def run_step(
    command: list[str], root: Path, log_path: Path, label: str, *, timeout: int = 2400
) -> None:
    """Bound installation time; log to disk, kill owned descendants and reap on error."""
    options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with log_path.open("ab") as output:
        output.write(f"\n--- {label} ---\n".encode())
        output.flush()
        with subprocess.Popen(
            command,
            cwd=root,
            env=clean_environment(root),
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **options,
        ) as child:
            try:
                started = time.monotonic()
                deadline = started + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        code = child.wait(timeout=min(20, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise
                        elapsed = int(time.monotonic() - started)
                        print(
                            f"  {label}... {elapsed // 60}m {elapsed % 60:02d}s elapsed", flush=True
                        )
                if code:
                    with log_path.open("rb") as receipt:
                        receipt.seek(max(0, log_path.stat().st_size - 8192))
                        tail = receipt.read(8192).decode("utf-8", errors="replace")
                    if "os error 4551" in tail or "Application Control policy has blocked" in tail:
                        raise SetupError(
                            f"Windows Application Control blocked the installed Python executable. Setup cannot change that policy. Review the block with your device administrator before continuing. Details: {log_path}."
                        )
                    raise SetupError(
                        f"{label} did not finish (exit {code}). See {log_path}. For downloads, check your internet connection and free disk space, then run Setup again."
                    )
            except subprocess.TimeoutExpired as exc:
                raise SetupError(
                    f"{label} took too long. See {log_path}, check your connection, and run Setup again."
                ) from exc
            finally:
                _stop(child, output)


def check_not_running(root: Path, *, ports=None) -> None:
    for port in (ports or installation_ports(root)).values():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise SetupError(
                    f"Port {port} is already in use. Stop the existing OpenAlgo app and research worker before setup or updating; then run Setup again."
                )


def setup(
    root: Path,
    uv: Path,
    *,
    broker=None,
    no_launch=False,
    no_browser=False,
    port=None,
    websocket_port=None,
    zmq_port=None,
    runner=run_step,
) -> int:
    root = root.resolve()
    verify_frontend(root)
    verify_release_files(root)
    ports = installation_ports(root, port=port, websocket_port=websocket_port, zmq_port=zmq_port)
    check_not_running(root, ports=ports)
    if not uv.is_file():
        raise SetupError(
            "The private installer is missing. Run Setup.cmd or bash setup-research.sh."
        )
    if sys.version_info[:2] != (3, 12):
        raise SetupError(
            "Setup needs Python 3.12. Run Setup.cmd or bash setup-research.sh to install it automatically."
        )
    from tools.research_desktop import InstanceLock

    log_path = root / "log/research-setup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only the previous setup attempt, rather than an unbounded history.
    if log_path.exists():
        log_path.replace(log_path.with_suffix(".previous.log"))
    (root / "tmp").mkdir(exist_ok=True)
    with InstanceLock(root / "tmp/research-desktop.lock"):
        print("[3/6] Installing the locked dependencies. The first setup can take several minutes.")
        print(f"Progress log: {log_path}", flush=True)
        runner(
            [
                str(uv),
                "sync",
                "--frozen",
                "--extra",
                "research",
                "--python",
                "3.12",
                "--managed-python",
            ],
            root,
            log_path,
            "Installing dependencies",
        )
        fresh = ensure_env(
            root, broker, port=port, websocket_port=websocket_port, zmq_port=zmq_port
        )
        print("[4/6] Checking the installed application...")
        # uv 0.12.5 reparses absolute Windows --env-file paths containing spaces.
        # Every step already has cwd=root, so a relative name selects the same
        # installation without passing its directory through that parser.
        command = [str(uv), "run", "--no-sync", "--env-file", ".env", "python"]
        runner(
            [*command, "tools/research_check.py"],
            root,
            log_path,
            "Checking compatibility",
            timeout=180,
        )
        print("[5/6] Preparing the native database tables...")
        (root / "db").mkdir(exist_ok=True)
        for migration in MIGRATIONS:
            runner(
                [*command, migration],
                root,
                log_path,
                f"Preparing {Path(migration).stem}",
                timeout=180,
            )
    print("[6/6] Ready. Use Start.cmd (Windows) or bash start-research.sh next time.")
    if fresh:
        print(
            "Create your OpenAlgo account in the browser. On Connect Broker, choose Add broker credentials, save your broker app keys, and restart with Start."
        )
    if no_launch:
        return 0
    python = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    launch = [str(python), str(root / "tools/research_desktop.py")]
    if no_browser:
        launch.append("--no-browser")
    # The desktop supervisor owns and cleans its child tree. Inherit the visible
    # console so Ctrl+C follows its normal graceful-shutdown path.
    with subprocess.Popen(launch, cwd=root, env=clean_environment(root)) as child:
        try:
            return child.wait()
        except KeyboardInterrupt:
            try:
                return child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                with log_path.open("ab") as output:
                    _stop(child, output)
                return 130


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--uv", type=Path, required=True, help="Private uv installed by the bootstrap"
    )
    parser.add_argument(
        "--broker", help="Broker name for a fresh unattended install; existing .env is preserved"
    )
    parser.add_argument(
        "--no-launch", action="store_true", help="Prepare the application without starting it"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Start the app without opening a browser"
    )
    parser.add_argument("--port", type=int, help="Fresh setup: local web port (default 5000)")
    parser.add_argument(
        "--websocket-port", type=int, help="Fresh setup: WebSocket port (default 8765)"
    )
    parser.add_argument("--zmq-port", type=int, help="Fresh setup: ZeroMQ port (default 5555)")
    args = parser.parse_args(argv)
    try:
        return setup(
            args.root,
            args.uv.resolve(),
            broker=args.broker,
            no_launch=args.no_launch,
            no_browser=args.no_browser,
            port=args.port,
            websocket_port=args.websocket_port,
            zmq_port=args.zmq_port,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"\nSetup stopped: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup cancelled. Run Setup again when ready.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
