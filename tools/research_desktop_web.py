"""Loopback-only SocketIO entry used by the managed desktop launcher."""

import os
import threading
from pathlib import Path


def run(runtime_dir: Path):
    # Refuse direct/misconfigured use before importing any native runtime.
    if os.environ.get("FLASK_HOST_IP") != "127.0.0.1" or os.environ.get("FLASK_DEBUG") != "False":
        raise RuntimeError("The desktop web entry requires loopback and debug disabled")
    token = os.environ.get("RESEARCH_DESKTOP_TOKEN", "")
    if len(token) != 48:
        raise RuntimeError("Start this app using the Research desktop launcher")
    from app import app, socketio
    from utils.shutdown import install_signal_handlers, shutdown_runtime

    install_signal_handlers()

    @app.get("/__research_desktop_ready")
    def desktop_ready():
        return token, 200, {"Content-Type": "text/plain", "Cache-Control": "no-store"}

    finished = threading.Event()

    def watch_stop():
        while not finished.wait(0.2):
            if (runtime_dir / "web.stop").exists():
                # The native shutdown routine must run on the main thread. CPython
                # delivers interrupt_main there, including on no-console Windows.
                import _thread
                import signal

                _thread.interrupt_main(signal.SIGINT)
                return

    watcher = threading.Thread(target=watch_stop, name="research-desktop-stop", daemon=True)
    watcher.start()
    try:
        # Werkzeug's noninteractive refusal otherwise rejects a redirected local
        # launcher. This permission is scoped here to loopback with no debugger.
        socketio.run(
            app,
            host="127.0.0.1",
            port=int(os.environ["FLASK_PORT"]),
            debug=False,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        finished.set()
        watcher.join(timeout=2)
        shutdown_runtime()
