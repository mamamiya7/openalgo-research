"""Run an explicitly named offline full-procedure calibration callback.

No broker access, scheduled work or live study is configured here. The selected
Python runner is trusted local code and must itself use isolated synthetic data.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.search_calibration import compile_plan, run, verify_receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runner", required=True, help="Explicit module:callback; receives (case, plan)"
    )
    parser.add_argument("--runner-identity", required=True)
    parser.add_argument("--code-identity", required=True)
    parser.add_argument(
        "--execution-model",
        choices=("controlled_account_model", "native_synthetic_market"),
        required=True,
    )
    parser.add_argument("--replicates", type=int, default=4)
    parser.add_argument(
        "--whole-procedure-seeds",
        action="store_true",
        help="Native only: canonical and two alternate whole-procedure sampler programmes",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    module_name, separator, function_name = args.runner.partition(":")
    if not separator or not module_name or not function_name or "." in function_name:
        parser.error("--runner requires module:callback")
    if args.output.exists():
        parser.error("Output already exists; retain prior evidence and choose a new output")
    plan_path = args.output.with_name(args.output.name + ".plan.json")
    observations = args.output.with_name(args.output.name + ".programmes")
    if plan_path.exists() or observations.exists():
        parser.error("Prior qualification evidence exists; choose a new output")
    if args.whole_procedure_seeds and args.execution_model != "native_synthetic_market":
        parser.error("Whole-procedure seed qualification requires native synthetic markets")
    # Disable ambient dotenv loading before importing an explicitly selected
    # runner. It remains the runner's duty not to access a normal installation.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    compiler, execute, verifier = compile_plan, run, verify_receipt
    if args.whole_procedure_seeds:
        from research import native_calibration

        compiler, execute, verifier = (
            native_calibration.compile_plan,
            native_calibration.run,
            native_calibration.verify_receipt,
        )
    declaration = {
        "code_identity": args.code_identity,
        "runner_identity": args.runner_identity,
        "replicates": args.replicates,
    }
    if not args.whole_procedure_seeds:
        declaration["execution_model"] = args.execution_model
    plan = compiler(**declaration)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Freeze before importing or executing the trusted runner. Interrupted work
    # keeps its declaration; a retry cannot overwrite or hide it.
    with plan_path.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, sort_keys=True, indent=2, allow_nan=False)
    callback = getattr(importlib.import_module(module_name), function_name)
    options = {}
    if args.whole_procedure_seeds:
        observations.mkdir()

        def retain(case, context, value):
            path = observations / f"{case['id']}-{context['index']}.json"
            with path.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)

        options["observe"] = retain
    receipt = verifier(execute(plan, callback, **options))
    # Exclusive creation avoids replacing a previous qualification receipt.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2, allow_nan=False)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "cases": len(receipt["cases"]),
                "scope": receipt["qualified_scope"],
            }
        )
    )
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
