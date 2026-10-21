"""Exercise release preparation before a workflow can request publishing credentials."""

import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

spec = importlib.util.spec_from_file_location("prepare_release", Path(__file__).with_name("prepare-release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class PrepareReleaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in release.PACKAGES.values():
            for name in ("Cargo.toml", "version.txt"):
                relative = Path(directory) / name
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((release.ROOT / relative).read_bytes())
        self.root.joinpath("Cargo.lock").write_bytes(release.ROOT.joinpath("Cargo.lock").read_bytes())
        # Tests start with coherent Cargo build inputs even after Runnerless has
        # proposed a newer version.txt without rewriting the source manifests.
        self.initial_versions = {}
        for package, directory in release.PACKAGES.items():
            version = tomllib.loads(self.root.joinpath(directory, "Cargo.toml").read_text())["package"]["version"]
            self.initial_versions[package] = version
            self.root.joinpath(directory, "version.txt").write_text(version + "\n")
        self.tag = "oci-zero-v" + self.initial_versions["oci-zero"]
        private = self.root / "web/Cargo.toml"
        private.parent.mkdir()
        private.write_bytes(release.ROOT.joinpath("web/Cargo.toml").read_bytes())

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_independent_versions_and_dependency_requirements(self):
        self.root.joinpath("gzip-zero/version.txt").write_text("0.2.0\n")
        self.root.joinpath("version.txt").write_text("0.3.0\n")
        private = self.root.joinpath("web/Cargo.toml").read_bytes()
        self.assertEqual(release.prepare(self.root, "gzip-zero-v0.2.0"), ("gzip-zero", "0.2.0"))
        manifest = tomllib.loads(self.root.joinpath("Cargo.toml").read_text())
        self.assertEqual(manifest["package"]["version"], "0.3.0")
        self.assertEqual(manifest["dependencies"]["gzip-zero"]["version"], "0.2.0")
        self.assertEqual(manifest["dependencies"]["zstd-zero"]["version"], self.initial_versions["zstd-zero"])
        self.assertEqual(self.root.joinpath("web/Cargo.toml").read_bytes(), private)
        locked = tomllib.loads(self.root.joinpath("Cargo.lock").read_text())["package"]
        versions = {p["name"]: p["version"] for p in locked if p["name"] in release.PACKAGES}
        self.assertEqual(versions, {"oci-zero": "0.3.0", "gzip-zero": "0.2.0", "zstd-zero": self.initial_versions["zstd-zero"]})

    def test_current_versions_are_unchanged_and_preparation_is_idempotent(self):
        before = self.snapshot()
        release.prepare(self.root, self.tag)
        self.assertEqual(self.snapshot(), before)
        release.prepare(self.root, self.tag)
        self.assertEqual(self.snapshot(), before)

    def test_rejects_wrong_tags_before_writing(self):
        before = self.snapshot()
        wrong_version = str(int(self.initial_versions["oci-zero"].split(".")[0]) + 1) + ".0.0"
        for tag in ("oci-zero-v" + wrong_version, "v0.2.1", "web-v0.0.1",
                    "oci-zero-v0.2.1-rc.1", "oci-zero-v00.2.1", "oci-zero-v0.2.1\n"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                release.prepare(self.root, tag)
            self.assertEqual(self.snapshot(), before)

    def test_rejects_malformed_other_crate_version_before_writing(self):
        self.root.joinpath("zstd-zero/version.txt").write_text("1.0.0-rc.1\n")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            release.prepare(self.root, self.tag)
        self.assertEqual(self.snapshot(), before)

    def test_rejects_wrong_package_identity_before_writing(self):
        path = self.root / "zstd-zero/Cargo.toml"
        path.write_text(path.read_text().replace('name = "zstd-zero"', 'name = "wrong-crate"'))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            release.prepare(self.root, self.tag)
        self.assertEqual(self.snapshot(), before)

    def test_rejects_missing_lock_identity_before_writing(self):
        path = self.root / "Cargo.lock"
        path.write_text(path.read_text().replace('name = "zstd-zero"', 'name = "wrong-crate"'))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            release.prepare(self.root, self.tag)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
