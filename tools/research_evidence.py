"""Install or verify the pinned public baseline without a development reference repo."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.evidence_import import DOCUMENTS_SHA256, IMPORT_VERSION, MANIFEST_SHA256

CATALOG = Path(__file__).resolve().parents[1] / "research" / "evidence_catalog"
CATALOGS = (
    ("nse_daily", "archives", MANIFEST_SHA256),
    ("exchange_evidence", "documents", DOCUMENTS_SHA256),
)
MAX_FILE_BYTES = 5_000_000


def catalog():
    """Only code-pinned metadata may choose filenames, URLs or expected content."""
    manifests, entries = {}, []
    for directory, key, expected in CATALOGS:
        raw = (CATALOG / f"{directory}.json").read_bytes()
        if len(raw) > 1_000_000 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f"Reviewed catalog failed integrity check: {directory}")
        manifests[directory] = raw
        for name, entry in json.loads(raw)[key].items():
            url = urlsplit(entry["source_url"])
            if (
                Path(name).name != name
                or "/" in name
                or "\\" in name
                or name in {".", "..", "manifest.json"}
                or url.scheme != "https"
                or url.hostname not in {"nsearchives.nseindia.com", "www.clcindia.com"}
                or url.username
                or url.password
                or url.port not in (None, 443)
                or not 0 < entry["bytes"] <= MAX_FILE_BYTES
            ):
                raise ValueError("Invalid file in reviewed source catalog")
            entries.append((Path(directory) / name, entry))
    return manifests, entries


def checked_local(root, relative):
    path = root / relative
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError(f"Symbolic links are not accepted in evidence: {relative}")
        current = current.parent
    if not path.resolve().is_relative_to(root):
        raise ValueError("Evidence path leaves the selected directory")
    return path


def verify_file(path, expected):
    if not path.is_file() or path.stat().st_size != expected["bytes"]:
        raise ValueError(f"Missing or incorrect file length: {path.name}")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(256 * 1024):
            total += len(chunk)
            if total > expected["bytes"]:
                raise ValueError(f"Source grew beyond its pinned length: {path.name}")
            digest.update(chunk)
    if digest.hexdigest() != expected["sha256"]:
        raise ValueError(f"Source file failed integrity check: {path.name}")


def verify(directory):
    root = Path(directory).resolve(strict=True)
    manifests, entries = catalog()
    for name, raw in manifests.items():
        verify_file(
            checked_local(root, Path(name) / "manifest.json"),
            {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        )
    for relative, entry in entries:
        verify_file(checked_local(root, relative), entry)
    daily = json.loads(manifests["nse_daily"])
    return {
        "status": "verified",
        "import_version": IMPORT_VERSION,
        "files": len(entries) + len(manifests),
        "bytes": sum(entry["bytes"] for _, entry in entries) + sum(map(len, manifests.values())),
        "date_from": daily["coverage_start"],
        "date_to": daily["coverage_end"],
        "manifest_sha256": MANIFEST_SHA256,
        "documents_manifest_sha256": DOCUMENTS_SHA256,
    }


def _copy(stream, output, expected):
    total = 0
    for chunk in stream:
        total += len(chunk)
        if total > expected["bytes"]:
            raise ValueError("Source response exceeds its pinned length")
        output.write(chunk)
    if total != expected["bytes"]:
        raise ValueError("Source response is shorter than its pinned length")


def install(destination, *, source=None, download=False):
    if (source is not None) == download:
        raise ValueError("Choose exactly one of --source or --download")
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise ValueError("Destination must be a new directory; existing evidence is never replaced")
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    origin = Path(source).resolve(strict=True) if source is not None else None
    manifests, entries = catalog()
    # Staging and the final rename share a filesystem. Failure leaves no partial
    # destination; TemporaryDirectory closes/removes only this call's own files.
    with tempfile.TemporaryDirectory(prefix=".research-evidence-", dir=parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir()
        for directory, raw in manifests.items():
            (stage / directory).mkdir()
            with (stage / directory / "manifest.json").open("xb") as output:
                output.write(raw)
        for relative, entry in entries:
            path = stage / relative
            with path.open("xb") as output:
                if origin is not None:
                    original = checked_local(origin, relative)
                    verify_file(original, entry)
                    with original.open("rb") as stream:
                        _copy(iter(lambda: stream.read(256 * 1024), b""), output, entry)
                else:
                    from utils.httpx_client import get_httpx_client

                    with get_httpx_client().stream(
                        "GET", entry["source_url"], timeout=20, follow_redirects=False
                    ) as response:
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise ValueError("Source did not return the pinned file (HTTP 200)")
                        _copy(response.iter_bytes(256 * 1024), output, entry)
                output.flush()
                os.fsync(output.fileno())
            verify_file(path, entry)
        receipt = verify(stage)
        if target.exists() or target.is_symlink():
            raise ValueError("Destination was created during installation; refusing to replace it")
        stage.rename(target)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--directory", required=True)
    prepare = commands.add_parser("install")
    prepare.add_argument("--destination", required=True)
    sources = prepare.add_mutually_exclusive_group(required=True)
    sources.add_argument("--source", help="Existing local archive bundle; copies only pinned files")
    sources.add_argument("--download", action="store_true", help="Fetch pinned URLs over HTTPS")
    args = parser.parse_args()
    try:
        result = (
            verify(args.directory)
            if args.command == "verify"
            else install(args.destination, source=args.source, download=args.download)
        )
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, httpx.HTTPError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
