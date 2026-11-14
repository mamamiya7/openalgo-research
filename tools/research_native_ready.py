"""Load the installed compiled stack before admitting historical calculations.

No prices, account data, broker clients or configuration are read. This is a
readiness check, not a financial test and not permission to bypass device policy.
"""
from __future__ import annotations

import importlib
import re


def classify_failure(text: str, launch_error: OSError | None = None) -> dict[str, str]:
    """Return a stable, non-secret diagnosis rather than echoing arbitrary logs."""
    lower = text.lower()
    winerror = getattr(launch_error, "winerror", None)
    policy = winerror in (577, 1260, 4551) or any(
        value in lower for value in (
            "os error 4551", "winerror 4551", "winerror 577", "winerror 1260",
            "application control policy", "blocked by group policy",
            "cannot verify the digital signature",
        )
    )
    native = bool(re.search(r"\.(?:dll|pyd|so|dylib)\b", lower)) or any(
        value in lower for value in ("llvmlite", "dll load failed", "loading shared libraries")
    )
    if policy and native and launch_error is None:
        return {
            "code": "native_library_blocked",
            "message": "Windows Application Control blocked a compiled research library. Python started, but the native library could not load. Ask your device administrator to review the blocked library; changing the Python launcher alone does not establish readiness. Setup does not change security policy.",
        }
    if policy:
        return {
            "code": "python_launcher_blocked",
            "message": "Windows Application Control blocked the Python launcher. Install signed 64-bit Python 3.12 from python.org and run Setup again, or review the block with your device administrator. Setup does not change security policy.",
        }
    if native and any(value in lower for value in ("not a valid win32", "wrong elf class", "winerror 193")):
        return {"code": "native_library_incompatible", "message": "A compiled research library is incompatible with this runtime. Use the release's supported 64-bit Python and locked dependencies; no research calculation was started."}
    if native and any(value in lower for value in ("cannot find", "could not find", "no such file", "dll load failed", "cannot open shared object")):
        return {"code": "native_library_missing", "message": "A compiled research library or one of its dependencies could not load. Re-run Setup with the supported runtime and locked dependencies; inspect the private log for the missing library. No research calculation was started."}
    if "modulenotfounderror" in lower or "importerror" in lower:
        return {"code": "dependency_import_failed", "message": "An installed research dependency could not import. Re-run Setup to restore the locked environment; inspect the private log for the failed module. No research calculation was started."}
    if launch_error is not None:
        return {"code": "process_start_failed", "message": "The setup command could not start. Check the private log, executable path and device permissions before retrying."}
    return {"code": "step_failed", "message": "The setup step did not finish. Inspect the private log before retrying."}


def check(import_module=importlib.import_module, output=print) -> None:
    # Importing llvmlite.binding loads its shared library. Importing vectorbt
    # exercises its real transitive native imports without evaluating a trade.
    for name in ("numpy", "llvmlite.binding", "numba", "vectorbt", "duckdb"):
        output(f"Native readiness: loading {name}", flush=True)
        module = import_module(name)
        if name == "llvmlite.binding":
            module.get_default_triple()
        elif name == "duckdb":
            with module.connect(":memory:") as connection:
                if connection.execute("SELECT 1").fetchone() != (1,):
                    raise RuntimeError("The native archive readiness query did not match")
    output("Native research libraries are ready. No financial calculation was run.", flush=True)


if __name__ == "__main__":
    check()
