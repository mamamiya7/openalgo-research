"""Package the unpacked Chrome connector with an explicit public-file allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from pathlib import Path

FILES = (
    "manifest.json",
    "background.js",
    "capture.js",
    "core.js",
    "openalgo-bridge.js",
    "chartink-button.js",
    "popup.html",
    "popup.css",
    "popup.js",
    "README.md",
    "CHANGELOG.md",
    "privacy.html",
    "privacy.css",
    "License.md",
    "NOTICE.md",
    "icons/logo.svg",
    "icons/icon16.png",
    "icons/icon32.png",
    "icons/icon48.png",
    "icons/icon128.png",
)


def validate(root):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    parts = version.split(".")
    if (
        not 1 <= len(parts) <= 4
        or any(
            not part.isascii()
            or not part.isdigit()
            or int(part) > 65535
            or (len(part) > 1 and part.startswith("0"))
            for part in parts
        )
        or not any(int(part) for part in parts)
    ):
        raise ValueError("Expected a valid numeric Chrome extension version")
    if json.loads((root / "package.json").read_text(encoding="utf-8"))["version"] != version:
        raise ValueError("Manifest and development package versions differ")
    for name in FILES:
        source = root / name
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError(f"Missing regular package file: {name}")
        if source.stat().st_size > 1024 * 1024:
            raise ValueError(f"Unexpectedly large package file: {name}")
    if (root / "License.md").read_bytes() != (root.parents[1] / "License.md").read_bytes():
        raise ValueError("Bundled license differs from the repository license")
    for icons in (manifest["icons"], manifest["action"]["default_icon"]):
        for size in (16, 32, 48, 128):
            name = icons[str(size)]
            if name not in FILES:
                raise ValueError(f"Icon is not in the package allowlist: {name}")
            data = (root / name).read_bytes()
            if data[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", data[16:24]) != (
                size,
                size,
            ):
                raise ValueError(f"Invalid PNG dimensions: {name}")
    return version


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=root.parents[1] / ".agent-native" / "chartink-package"
    )
    args = parser.parse_args()
    version = validate(root)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / f"openalgo-chartink-{version}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in FILES:
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, (root / name).read_bytes())
    with zipfile.ZipFile(target) as bundle:
        if bundle.testzip() is not None or bundle.namelist() != list(FILES):
            raise ValueError("Package verification failed")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    print(json.dumps({"package": str(target.resolve()), "files": len(FILES), "sha256": digest}))


if __name__ == "__main__":
    main()
