"""Isolated optional Optuna plotting; no market-data or database services."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.study_diagnostics import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    VERSION,
    payload,
    validate_result,
)


def main():
    directory = Path(sys.argv[1]).resolve()
    incoming = directory / "input.json"
    response = {"version": VERSION}
    try:
        if not incoming.is_file() or incoming.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Recorded diagnostics input exceeds its limit")
        request = json.loads(incoming.read_text(encoding="utf-8"))
        if not isinstance(request, dict) or set(request) != {"experiment", "parameters"}:
            raise ValueError("Invalid recorded diagnostics request")
        request = payload(request["experiment"], request["parameters"])
        from research.study_analysis import build_study_analysis

        response["result"] = validate_result(
            build_study_analysis(request["experiment"], parameters=request["parameters"])
        )
        raw = json.dumps(response, allow_nan=False).encode()
        if len(raw) > MAX_OUTPUT_BYTES:
            raise ValueError("Optional study charts exceed their saved size limit")
    except Exception as error:
        response = {"version": VERSION, "error": str(error)[:500]}
        raw = json.dumps(response).encode()
    (directory / "output.json").write_bytes(raw)
    return 1 if response.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
