"""Pinned source provisioning is portable, bounded and leaves no partial bundle."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tools import research_evidence as evidence


@pytest.fixture
def tiny_catalog(tmp_path, monkeypatch):
    metadata = tmp_path / "catalog"
    origin = tmp_path / "origin"
    metadata.mkdir()
    origin.mkdir()
    definitions = []
    for directory, key, name, payload in (
        ("nse_daily", "archives", "20251205.zip", b"pinned archive"),
        ("exchange_evidence", "documents", "notice.pdf", b"pinned notice"),
    ):
        (origin / directory).mkdir()
        (origin / directory / name).write_bytes(payload)
        manifest = {
            "coverage_start": "2025-12-05",
            "coverage_end": "2025-12-05",
            key: {
                name: {
                    "source_url": f"https://nsearchives.nseindia.com/{name}",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            },
        }
        raw = json.dumps(manifest).encode()
        (metadata / f"{directory}.json").write_bytes(raw)
        definitions.append((directory, key, hashlib.sha256(raw).hexdigest()))
    monkeypatch.setattr(evidence, "CATALOG", metadata)
    monkeypatch.setattr(evidence, "CATALOGS", definitions)
    return origin, metadata


def test_repository_catalog_preserves_exact_importer_identities():
    manifests, entries = evidence.catalog()
    assert len(entries) == 199
    assert hashlib.sha256(manifests["nse_daily"]).hexdigest() == evidence.MANIFEST_SHA256
    assert hashlib.sha256(manifests["exchange_evidence"]).hexdigest() == evidence.DOCUMENTS_SHA256


def test_install_copies_only_pinned_files_and_can_verify_without_origin(tmp_path, tiny_catalog):
    origin, _ = tiny_catalog
    (origin / "private.env").write_text("must remain private")
    destination = tmp_path / "portable"
    receipt = evidence.install(destination, source=origin)
    (origin / "nse_daily" / "20251205.zip").unlink()
    assert receipt == evidence.verify(destination)
    assert receipt["files"] == 4
    assert not (destination / "private.env").exists()
    assert len(list(destination.rglob("*.*"))) == 4


@pytest.mark.parametrize("failure", ["missing", "corrupt", "catalog"])
def test_failed_install_leaves_no_destination_or_staging_files(tmp_path, tiny_catalog, failure):
    origin, metadata = tiny_catalog
    if failure == "missing":
        (origin / "exchange_evidence" / "notice.pdf").unlink()
    elif failure == "corrupt":
        (origin / "exchange_evidence" / "notice.pdf").write_bytes(b"wrong  notice")
    else:
        (metadata / "nse_daily.json").write_bytes(b"{}")
    with pytest.raises((ValueError, FileNotFoundError)):
        evidence.install(tmp_path / "portable", source=origin)
    assert not (tmp_path / "portable").exists()
    assert not list(tmp_path.glob(".research-evidence-*"))


def test_existing_destination_is_never_overwritten(tmp_path, tiny_catalog):
    origin, _ = tiny_catalog
    destination = tmp_path / "portable"
    destination.mkdir()
    marker = destination / "saved-evidence"
    marker.write_bytes(b"retain me")
    with pytest.raises(ValueError, match="new directory"):
        evidence.install(destination, source=origin)
    assert marker.read_bytes() == b"retain me"


def test_symlinked_source_file_cannot_escape_bundle(tmp_path, tiny_catalog):
    origin, _ = tiny_catalog
    target = origin / "exchange_evidence" / "notice.pdf"
    external = tmp_path / "outside.pdf"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("Host cannot create symbolic links without additional privilege")
    with pytest.raises(ValueError, match="Symbolic links"):
        evidence.install(tmp_path / "portable", source=origin)


@pytest.mark.parametrize("outcome", ["success", "redirect", "oversize", "timeout", "corrupt"])
def test_download_verifies_bytes_closes_responses_and_cleans_failures(
    tmp_path, tiny_catalog, monkeypatch, outcome
):
    origin, _ = tiny_catalog
    responses = []

    def handle(request):
        name = Path(request.url.path).name
        directory = "nse_daily" if name.endswith(".zip") else "exchange_evidence"
        body = (origin / directory / name).read_bytes()
        if outcome == "timeout":
            raise httpx.ReadTimeout("Fixture timeout", request=request)
        if outcome == "oversize":
            body += b"too much"
        if outcome == "corrupt":
            body = b"x" * len(body)
        response = httpx.Response(
            302 if outcome == "redirect" else 200,
            content=body,
            headers={"Location": "https://example.invalid/redirect"},
        )
        responses.append(response)
        return response

    destination = tmp_path / "downloaded"
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr("utils.httpx_client.get_httpx_client", lambda: client)
        if outcome == "success":
            assert evidence.install(destination, download=True)["files"] == 4
        else:
            with pytest.raises((ValueError, httpx.HTTPError)):
                evidence.install(destination, download=True)
            assert not destination.exists()
    assert all(response.is_closed for response in responses)
    assert not list(tmp_path.glob(".research-evidence-*"))
