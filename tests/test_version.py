"""The version number must be the same everywhere it appears."""

import re
import tomllib
import unittest
from pathlib import Path

import aulari

ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    """aulari.__version__, pyproject.toml and the Windows version resource agree."""

    def test_versions_match(self) -> None:
        """A release tag vX.Y.Z needs the same X.Y.Z in every file."""
        with (ROOT / "pyproject.toml").open("rb") as project_file:
            project_version = tomllib.load(project_file)["project"]["version"]
        resource = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
        strings = set(re.findall(r"'(?:File|Product)Version', '([0-9.]+)'", resource))
        major, minor, patch = (int(part) for part in aulari.__version__.split("."))
        self.assertEqual(project_version, aulari.__version__)
        self.assertEqual(strings, {aulari.__version__})
        self.assertIn(f"filevers=({major}, {minor}, {patch}, 0)", resource)
