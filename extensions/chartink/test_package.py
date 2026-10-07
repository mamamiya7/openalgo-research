"""Public ZIP contract checks; no browser account or application data is used."""

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("chartink_package", ROOT / "package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PublicPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name)
        self.root = self.checkout / "extensions" / "chartink"
        self.root.mkdir(parents=True)
        for name in (*PACKAGE.FILES, "package.json", "package.py"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        shutil.copyfile(ROOT.parents[1] / "License.md", self.checkout / "License.md")

    def build(self):
        output = self.checkout / "output"
        with (
            patch.object(PACKAGE, "__file__", str(self.root / "package.py")),
            patch.object(sys, "argv", ["package.py", "--output", str(output)]),
            redirect_stdout(io.StringIO()),
        ):
            PACKAGE.main()
        return output / "openalgo-chartink-0.1.1.zip"

    def test_store_layout_assets_privacy_license_and_exclusions(self):
        (self.root / ".env").write_text("private=never-ship")
        (self.root / "private-export.csv").write_text("Date,Symbol\n")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules/private.js").write_text("do_not_ship")
        target = self.build()
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertIn("manifest.json", names)
            self.assertNotIn(".env", names)
            self.assertNotIn("private-export.csv", names)
            self.assertFalse(any("node_modules" in name for name in names))
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["version"], "0.1.1")
            for section in (manifest["icons"], manifest["action"]["default_icon"]):
                for path in section.values():
                    self.assertIn(path, names)
            self.assertIn(b"privacy.html", archive.read("popup.html"))
            self.assertIn("privacy.html", names)
            self.assertIn("NOTICE.md", names)
            self.assertEqual(
                archive.read("License.md"), (self.checkout / "License.md").read_bytes()
            )

    def test_reproducible_archive_and_matching_checksum(self):
        target = self.build()
        first = target.read_bytes()
        self.assertEqual(first, self.build().read_bytes())
        self.assertEqual(
            (target.parent / "SHA256SUMS").read_text().strip(),
            f"{hashlib.sha256(first).hexdigest()}  {target.name}",
        )

    def test_missing_icon_is_rejected(self):
        (self.root / "icons/icon48.png").unlink()
        with self.assertRaisesRegex(ValueError, "Missing regular package file"):
            self.build()

    def test_wrong_icon_dimensions_are_rejected(self):
        shutil.copyfile(self.root / "icons/icon16.png", self.root / "icons/icon128.png")
        with self.assertRaisesRegex(ValueError, "Invalid PNG dimensions"):
            self.build()

    def test_changed_license_is_rejected(self):
        (self.root / "License.md").write_text("Incorrect license")
        with self.assertRaisesRegex(ValueError, "Bundled license differs"):
            self.build()

    def test_invalid_and_inconsistent_versions_are_rejected(self):
        path = self.root / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for version in ("../secret", "0.01.1", "0.1.65536", "0.0.0.0", "1.2.3.4.5", "0.1.2"):
            with self.subTest(version=version):
                path.write_text(json.dumps({**manifest, "version": version}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.build()


if __name__ == "__main__":
    unittest.main()
