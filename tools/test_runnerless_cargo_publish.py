import hashlib
import contextlib
from types import SimpleNamespace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("publisher", Path(__file__).with_name("runnerless-cargo-publish.py"))
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)

class CargoOidcTest(unittest.TestCase):
    def setUp(self):
        publisher.DEADLINE = None

    def tearDown(self):
        publisher.DEADLINE = None

    def test_digest_reconciliation_and_immutable_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            crate = Path(temporary) / "gzip-zero-0.2.0.crate"
            crate.write_bytes(b"test archive")
            entry = {"cksum": hashlib.sha256(crate.read_bytes()).hexdigest(), "yanked": False}
            with patch.object(publisher, "index", return_value=entry):
                self.assertTrue(publisher.reconcile("gzip-zero", "0.2.0", crate))
                crate.write_bytes(b"changed archive")
                with self.assertRaisesRegex(ValueError, "digest_conflict"):
                    publisher.reconcile("gzip-zero", "0.2.0", crate)
            with patch.object(publisher, "index", return_value=None):
                self.assertFalse(publisher.reconcile("gzip-zero", "0.2.0", crate))

    def test_waits_for_actual_dependency_before_dependent_publication(self):
        metadata = {"workspace_root": "/repo", "packages": [{"name": "gzip-zero", "version": "0.2.0", "manifest_path": "/repo/gzip-zero/Cargo.toml", "dependencies": []}]}
        package = {"dependencies": [{"path": "/repo/gzip-zero", "name": "alias", "kind": None}]}
        with patch.object(publisher, "index", side_effect=[None, {"yanked": False}]) as lookup, patch.object(publisher.time, "sleep") as sleep, patch.object(publisher, "run", side_effect=["a" * 40, "b" * 40, '[package]\nname="gzip-zero"\nversion="0.2.0"']), patch.object(publisher, "release_revision", return_value="a" * 40), patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"), patch.object(publisher.subprocess, "run"):
            publisher.wait_dependencies(package, metadata)
            self.assertEqual(lookup.call_args_list[0].args, ("gzip-zero", "0.2.0"))
            self.assertEqual(lookup.call_count, 2)
            sleep.assert_called_once_with(10)
        with patch.object(publisher, "index", return_value=None), patch.object(publisher.time, "sleep"), patch.object(publisher, "run", side_effect=["a" * 40, "b" * 40, '[package]\nname="gzip-zero"\nversion="0.2.0"']), patch.object(publisher, "release_revision", return_value="a" * 40), patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"), patch.object(publisher.subprocess, "run"):
            with self.assertRaisesRegex(RuntimeError, "dependency_not_visible"):
                publisher.wait_dependencies(package, metadata)

    def test_visible_dependency_with_conflicting_release_bytes_blocks_dependent(self):
        metadata = {"workspace_root": "/repo", "target_directory": "/repo/target", "packages": [{"name": "gzip-zero", "version": "0.2.0", "manifest_path": "/repo/gzip-zero/Cargo.toml", "dependencies": []}]}
        package = {"dependencies": [{"path": "/repo/gzip-zero", "kind": None}]}
        with patch.object(publisher, "run", side_effect=["a" * 40, "a" * 40, '[package]\nname="gzip-zero"\nversion="0.2.0"']), patch.object(publisher, "release_revision", return_value="a" * 40), patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"), patch.object(publisher.subprocess, "run"), patch.object(publisher, "reconcile", side_effect=ValueError("immutable_registry_digest_conflict")):
            with self.assertRaisesRegex(ValueError, "digest_conflict"):
                publisher.wait_dependencies(package, metadata)

    def test_binds_release_to_repository_stable_tag_manifest_and_exact_revision(self):
        sha = "a" * 40
        responses = [json.dumps({"tag_name": "gzip-zero-v0.2.0", "draft": False, "prerelease": False}), sha, sha, json.dumps({"sha": sha}),
                     json.dumps({"default_branch": "main"}), json.dumps({"sha": "b" * 40}), json.dumps({"status": "ahead"}), json.dumps({"packages": [{"name": "gzip-zero", "version": "0.2.0", "id": "p", "publish": None}], "workspace_members": ["p"]})]
        with patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"), patch.object(publisher, "run", side_effect=responses) as commands:
            name, version, _, _ = publisher.select("fixture/repo", "gzip-zero-v0.2.0", ["gzip-zero"])
            self.assertEqual((name, version), ("gzip-zero", "0.2.0"))
            self.assertFalse(any(call.args[:2] == ("git", "fetch") for call in commands.call_args_list))
            self.assertIn(("gh", "api", "repos/fixture/repo/compare/" + sha + "..." + "b" * 40), [call.args for call in commands.call_args_list])
        with patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"):
            for repository, tag in [("foreign/repo", "gzip-zero-v0.2.0"), ("fixture/repo", "gzip-zero-v0.2.0-beta"), ("fixture/repo", "other-v0.2.0")]:
                with self.assertRaises(ValueError):
                    publisher.select(repository, tag, ["gzip-zero"])

    def test_moved_remote_tag_blocks_publication_after_checkout(self):
        sha = "a" * 40
        responses = [json.dumps({"tag_name": "gzip-zero-v0.2.0", "draft": False, "prerelease": False}), sha, sha, json.dumps({"sha": "b" * 40})]
        with patch.dict(os.environ, GITHUB_REPOSITORY="fixture/repo"), patch.object(publisher, "run", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "remote_release_tag_changed"):
                publisher.select("fixture/repo", "gzip-zero-v0.2.0", ["gzip-zero"])

    def test_registry_allowlist_and_explicit_cargo_destination(self):
        for destinations in [[], ["internal"]]:
            with self.assertRaisesRegex(ValueError, "manifest_disallows_crates_io"):
                publisher.require_crates_io({"publish": destinations})
        for destinations in [None, ["crates-io"], ["internal", "crates-io"]]:
            publisher.require_crates_io({"publish": destinations})
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "package" / "Pkg-1.0.0.crate"
            archive.parent.mkdir()
            archive.write_bytes(b"archive")
            # A truthy sentinel tests credential presence without a token fixture.
            with patch.object(publisher, "select", return_value=("Pkg", "1.0.0", {}, {"target_directory": temporary})), patch.object(publisher, "reconcile", side_effect=[False, True]), patch.object(publisher.subprocess, "run") as command, patch.object(publisher.os, "environ", {"CARGO_REGISTRY_TOKEN": True}), patch("sys.argv", ["publish.py", "publish", "--repository", "fixture/repo", "--tag", "Pkg-v1.0.0", "--crate", "Pkg"]):
                publisher.main()
                self.assertEqual(command.call_args.args[0], ["cargo", "publish", "--locked", "--registry", "crates-io", "-p", "Pkg"])

    def test_timed_out_upload_reconciles_before_retrying(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "package" / "Pkg-1.0.0.crate"
            archive.parent.mkdir()
            archive.write_bytes(b"archive")
            for published in [True, False]:
                with self.subTest(published=published), patch.object(publisher, "select", return_value=("Pkg", "1.0.0", {}, {"target_directory": temporary})), patch.object(publisher, "reconcile", side_effect=[False, True] if published else None, return_value=False) as reconcile, patch.object(publisher.subprocess, "run", side_effect=publisher.subprocess.TimeoutExpired("cargo", 600)) as command, patch.object(publisher.time, "sleep"), patch.object(publisher.os, "environ", {"CARGO_REGISTRY_TOKEN": True}), patch("sys.argv", ["publish.py", "publish", "--repository", "fixture/repo", "--tag", "Pkg-v1.0.0", "--crate", "Pkg"]):
                    if published:
                        publisher.main()
                        self.assertEqual(reconcile.call_count, 2)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "cargo_upload_failed_reconcile_before_retry"):
                            publisher.main()
                        self.assertEqual(reconcile.call_count, 14)
                    command.assert_called_once()

    def response(self, status, chunks):
        response = SimpleNamespace(status=status, read1=Mock(side_effect=chunks))
        connection = SimpleNamespace(close=Mock(), request=Mock(), sock=SimpleNamespace(settimeout=Mock()), getresponse=lambda: contextlib.nullcontext(response))
        return connection

    def test_mixed_case_identity_uses_lowercase_index_path(self):
        body = json.dumps({"name": "Mixed_Crate", "vers": "1.2.3", "cksum": "a" * 64, "yanked": False}).encode()
        connection = self.response(200, [body, b""])
        with patch.object(publisher.http.client, "HTTPSConnection", return_value=connection):
            self.assertEqual(publisher.index("Mixed_Crate", "1.2.3")["name"], "Mixed_Crate")
            self.assertEqual(connection.request.call_args.args, ("GET", "/mi/xe/mixed_crate"))

    def test_shared_deadline_bounds_all_calls_and_expires_before_outer_job(self):
        with patch.object(publisher.time, "time", return_value=100):
            publisher.DEADLINE = 115
            self.assertEqual(publisher.remaining(600), 15)
            self.assertEqual(publisher.remaining(30, deadline=105), 5)
            publisher.DEADLINE = 99
            with self.assertRaisesRegex(RuntimeError, "release_deadline_exhausted"):
                publisher.run("cargo", "publish")

    def test_registry_outage_is_not_absent_and_redirects_are_refused(self):
        for code, expected in [(404, None), (503, RuntimeError), (302, RuntimeError)]:
            with patch.object(publisher.http.client, "HTTPSConnection", return_value=self.response(code, [])):
                if expected is None:
                    self.assertIsNone(publisher.index("gzip-zero", "0.2.0"))
                else:
                    with self.assertRaises(expected):
                        publisher.index("gzip-zero", "0.2.0")

    def test_local_deadlines_keep_their_specific_diagnostic(self):
        with patch.object(publisher.time, "time", return_value=100):
            publisher.DEADLINE = 1000
            for reason in ["dependency_not_visible_retry_after_dependency_release", "registry_visibility_timeout_retry_same_revision"]:
                with self.assertRaisesRegex(RuntimeError, reason):
                    publisher.remaining(30, deadline=99, timeout_reason=reason)

    def test_slow_headers_cannot_outlive_the_whole_request_deadline(self):
        connection = self.response(200, [])
        def slow_headers():
            publisher.time.sleep(0.2)
            raise AssertionError("wall timer did not interrupt header parsing")
        connection.getresponse = slow_headers
        with patch.object(publisher.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "dependency_not_visible"):
                publisher.index("gzip-zero", "0.2.0", deadline=publisher.time.time() + 0.02,
                                timeout_reason="dependency_not_visible_retry_after_dependency_release")
        connection.close.assert_called_once()

    def test_dribbling_response_cannot_outlive_the_shared_deadline(self):
        clock = [100]
        def read(limit):
            clock[0] += 4
            return b" "
        connection = self.response(200, [])
        connection.getresponse = lambda: contextlib.nullcontext(SimpleNamespace(status=200, read1=read))
        with patch.object(publisher.time, "time", side_effect=lambda: clock[0]), patch.object(publisher.http.client, "HTTPSConnection", return_value=connection):
            publisher.DEADLINE = 110
            with self.assertRaisesRegex(RuntimeError, "release_deadline_exhausted"):
                publisher.index("gzip-zero", "0.2.0")
            self.assertEqual([call.args[0] for call in connection.sock.settimeout.call_args_list], [10, 10, 6, 2])

if __name__ == "__main__":
    unittest.main()
