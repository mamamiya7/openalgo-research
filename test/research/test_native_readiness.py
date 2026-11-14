"""Native-load preflight/classification; no real broker, imports or calculations."""
from types import SimpleNamespace

import pytest

from tools import research_native_ready as ready


@pytest.mark.parametrize("text,code", [
    ("An Application Control policy has blocked this file. (os error 4551)", "python_launcher_blocked"),
    ("OSError: cannot load libllvmlite.dll: Windows cannot verify the digital signature (WinError 577)", "native_library_blocked"),
    ("ImportError: DLL load failed importing _multiarray_umath: module could not be found", "native_library_missing"),
    ("OSError: LLVM.dll is not a valid Win32 application (WinError 193)", "native_library_incompatible"),
    ("ModuleNotFoundError: No module named vectorbt", "dependency_import_failed"),
    ("AssertionError: Financial validation failed", "step_failed"),
])
def test_native_failure_classification_does_not_hide_validation_or_suggest_policy_bypass(text, code):
    result = ready.classify_failure(text)
    assert result["code"] == code
    assert "Financial validation failed" not in result["message"]
    assert "Set-ExecutionPolicy" not in result["message"]


def test_a_blocked_executable_is_not_misdiagnosed_as_an_imported_library():
    error = OSError("Blocked")
    error.winerror = 4551
    assert ready.classify_failure("", error)["code"] == "python_launcher_blocked"


def test_readiness_loads_every_native_dependency_and_closes_archive_connection():
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): calls.append("closed")
        def execute(self, sql):
            assert sql == "SELECT 1"
            return self
        def fetchone(self): return (1,)
    def imported(name):
        calls.append(name)
        return SimpleNamespace(get_default_triple=lambda: "x86_64", connect=lambda path: Connection())
    ready.check(import_module=imported, output=lambda *a, **k: None)
    assert calls == ["numpy", "llvmlite.binding", "numba", "vectorbt", "duckdb", "closed"]


def test_native_import_refusal_stops_before_other_libraries_and_calculations():
    calls = []
    def imported(name):
        calls.append(name)
        raise OSError("Application Control blocked libllvmlite.dll")
    with pytest.raises(OSError, match="Application Control"):
        ready.check(import_module=imported, output=lambda *a, **k: None)
    assert calls == ["numpy"]
