#!/usr/bin/env bash
set -eu
task_root="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -x "$task_root/.venv/bin/python" ]; then
    printf '%s\n' 'Run bash setup-research.sh first. It installs everything OpenAlgo Research needs.' >&2
    exit 1
fi
cd "$task_root"
exec "$task_root/.venv/bin/python" "$task_root/tools/research_desktop.py" "$@"
