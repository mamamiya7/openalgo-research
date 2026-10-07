#!/usr/bin/env bash
# Private, pinned bootstrap. Run with: bash setup-research.sh
set -eu
task_root="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
runtime="$task_root/.research-runtime"
uv="$runtime/bin/uv"
if [ ! -f "$task_root/frontend/dist/index.html" ]; then
    printf '%s\n' 'The built interface is missing. Download the openalgo-research ZIP from GitHub Releases, not Source code.' 'Developers: run npm ci and npm run build inside frontend first.' >&2
    exit 1
fi
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'This installer supports Linux. On Windows, double-click Setup.cmd.' >&2
    exit 1
fi
if [ ! -x "$uv" ]; then
    case "$(uname -m)" in
        x86_64) asset=uv-x86_64-unknown-linux-gnu.tar.gz; expected=68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2 ;;
        *) printf '%s\n' 'This release supports x86_64 Linux. Other architectures are not supported by the locked research dependencies.' >&2; exit 1 ;;
    esac
    for command in curl tar sha256sum; do
        if ! command -v "$command" >/dev/null 2>&1; then
            printf 'Install %s with your Linux package manager, then run this setup again.\n' "$command" >&2
            exit 1
        fi
    done
    printf '%s\n' '[1/6] Downloading the private installer (uv 0.12.5)...'
    mkdir -p "$runtime/bin"
    download="$(mktemp -d "$runtime/download-XXXXXXXX")"
    # This trap removes only files in the newly-created, known temporary directory.
    trap 'rm -f -- "$download/archive.tar.gz"; rm -rf -- "$download/unpacked"; rmdir -- "$download"' EXIT
    if ! curl --fail --location --proto '=https' --tlsv1.2 --connect-timeout 30 --max-time 240 --output "$download/archive.tar.gz" "https://github.com/astral-sh/uv/releases/download/0.12.5/$asset"; then
        printf '%s\n' 'Could not download the installer. Check your connection to github.com and retry.' >&2
        exit 1
    fi
    actual="$(sha256sum "$download/archive.tar.gz")"
    if [ "${actual%% *}" != "$expected" ]; then
        printf '%s\n' 'Installer integrity check failed. Nothing was installed. Check your connection and retry.' >&2
        exit 1
    fi
    mkdir "$download/unpacked"
    tar -xzf "$download/archive.tar.gz" -C "$download/unpacked"
    cp "$download/unpacked/${asset%.tar.gz}/uv" "$uv"
    chmod 700 "$uv"
fi
case "$("$uv" --version)" in
    'uv 0.12.5'|'uv 0.12.5 '*) ;;
    *) printf '%s\n' 'The private installer is not uv 0.12.5. Remove .research-runtime/bin/uv and retry.' >&2; exit 1 ;;
esac
export UV_PYTHON_INSTALL_DIR="$runtime/python"
export UV_CACHE_DIR="$runtime/cache"
# Use the CLI managed-Python request only; inherited preferences conflict with it.
unset UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON
export UV_NO_PROGRESS=1
printf '%s\n' '[2/6] Preparing Python 3.12 (first setup needs an internet connection)...'
"$uv" --directory "$task_root" run --no-project --managed-python --python 3.12 python "$task_root/tools/research_setup.py" --uv "$uv" "$@"
