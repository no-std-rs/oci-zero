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

class CargoPreparationTest(unittest.TestCase):
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
                     json.dumps({"default_branch": "main"}), json.dumps({"sha": "b" * 40}), json.dumps({"status": "ahead"}), "", json.dumps({"packages": [{"name": "gzip-zero", "version": "0.2.0", "id": "p", "publish": None}], "workspace_members": ["p"]})]
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
            output = Path(temporary) / "outputs"
            with patch.object(publisher, "select", return_value=("Pkg", "1.0.0", {"dependencies": []}, {"target_directory": temporary,"packages":[]})), patch.object(publisher, "reconcile", return_value=False), patch.object(publisher.subprocess, "run") as command, patch.dict(os.environ, GITHUB_OUTPUT=str(output)), patch("sys.argv", ["prepare.py", "package", "--repository", "fixture/repo", "--tag", "Pkg-v1.0.0", "--crate", "Pkg"]):
                publisher.main()
                self.assertEqual(command.call_args.args[0], ["cargo", "package", "--locked", "--registry", "crates-io", "-p", "Pkg"])
                self.assertIn("publish=true", output.read_text())

    def test_attachment_reconciles_matching_bytes_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            asset = directory / "Pkg-1.0.0.cargo-upload"
            asset.write_bytes(b"verified cargo body")
            (directory / "SHA256SUMS").write_text(hashlib.sha256(asset.read_bytes()).hexdigest() + "  " + asset.name + "\n")
            files = [asset, directory / "SHA256SUMS"]
            assets = [{"id": n, "name": f.name, "size": f.stat().st_size,
                       "digest": "sha256:" + hashlib.sha256(f.read_bytes()).hexdigest()} for n,f in enumerate(files)]
            release = {"tag_name":"Pkg-v1.0.0","draft":False,"prerelease":False,"published_at":"2026-10-23T00:00:00Z","assets":assets}
            with patch.object(publisher, "run", return_value=json.dumps(release)), patch.object(publisher.subprocess, "run") as upload:
                publisher.attach("fixture/repo", "Pkg-v1.0.0", "Pkg", "1.0.0", directory)
                upload.assert_not_called()
            # A missing manifest can be added after an interrupted first attachment.
            release["assets"] = assets[:1]
            with patch.object(publisher, "run", return_value=json.dumps(release)), patch.object(publisher.subprocess, "run") as upload:
                publisher.attach("fixture/repo", "Pkg-v1.0.0", "Pkg", "1.0.0", directory)
                self.assertEqual(upload.call_args.args[0], ["gh", "release", "upload", "Pkg-v1.0.0", str(files[1]), "--repo", "fixture/repo"])
            # Preflight a later conflicting asset before uploading any earlier missing one.
            release["assets"] = [dict(assets[1], digest="sha256:" + "0" * 64)]
            with patch.object(publisher, "run", return_value=json.dumps(release)), patch.object(publisher.subprocess, "run") as upload:
                with self.assertRaisesRegex(ValueError, "immutable_release_asset_conflict"):
                    publisher.attach("fixture/repo", "Pkg-v1.0.0", "Pkg", "1.0.0", directory)
                upload.assert_not_called()

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
                publisher.run("cargo", "package")

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



capture_spec = importlib.util.spec_from_file_location("cargo_capture", Path(__file__).with_name("runnerless-cargo-capture.py"))
capture = importlib.util.module_from_spec(capture_spec)
capture_spec.loader.exec_module(capture)

class CargoCaptureTest(unittest.TestCase):
    def body(self, archive, name="oci-zero", version="0.3.0"):
        import struct
        metadata = json.dumps({"name":name,"vers":version,"deps":[],"features":{}}).encode()
        return struct.pack("<I",len(metadata)) + metadata + struct.pack("<I",len(archive)) + archive

    def test_capture_refuses_another_package_version_or_archive(self):
        archive=b"canonical crate"
        body=self.body(archive)
        expected=hashlib.sha256(archive).hexdigest()
        capture.parse_upload(body,"oci-zero","0.3.0",expected)
        for name,version,digest in [("gzip-zero","0.3.0",expected),("oci-zero","0.4.0",expected),("oci-zero","0.3.0","0"*64)]:
            with self.assertRaises(ValueError):
                capture.parse_upload(body,name,version,digest)
        for truncated in [body[:4],body[:-1],body+b"extra"]:
            with self.assertRaises(ValueError):
                capture.parse_upload(truncated,"oci-zero","0.3.0",expected)

    def test_actual_loopback_capture_keeps_independent_version_and_canonical_archive(self):
        import urllib.request
        with tempfile.TemporaryDirectory() as temporary:
            archive=Path(temporary)/"oci-zero-0.3.0.crate"
            archive.write_bytes(b"canonical crate")
            output=Path(temporary)/"assets"
            def cargo(command, **kwargs):
                self.assertIn("--locked",command)
                self.assertNotIn("--allow-dirty",command)
                self.assertIn("runnerless-capture",command)
                env=kwargs["env"]
                self.assertNotIn("CARGO_REGISTRY_TOKEN",env)
                index=env["CARGO_REGISTRIES_RUNNERLESS_CAPTURE_INDEX"].removeprefix("sparse+")
                # The selected crate is hidden locally even if already on crates.io.
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(index + capture.index_path("oci-zero"))
                self.assertEqual(missing.exception.code,404)
                missing.exception.close()
                body=self.body(archive.read_bytes())
                request=urllib.request.Request(index.removesuffix("/index/")+"/api/v1/crates/new",data=body,method="PUT",headers={"Authorization":env["CARGO_REGISTRIES_RUNNERLESS_CAPTURE_TOKEN"]})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(response.status,200)
                return SimpleNamespace(returncode=0)
            with patch.object(capture.subprocess,"run",side_effect=cargo), patch.object(capture.os,"environ",{"CARGO_REGISTRY_TOKEN":True}):
                capture.capture("oci-zero","0.3.0",archive,output,5)
            asset=output/"oci-zero-0.3.0.cargo-upload"
            self.assertEqual(asset.read_bytes(),self.body(archive.read_bytes()))
            self.assertEqual((output/"SHA256SUMS").read_text(),hashlib.sha256(asset.read_bytes()).hexdigest()+"  "+asset.name+"\n")


if __name__ == "__main__":
    unittest.main()
