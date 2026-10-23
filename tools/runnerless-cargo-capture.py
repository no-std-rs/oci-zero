#!/usr/bin/env python3
"""Capture Cargo publish requests locally for Runnerless to relay to crates.io.

The local registry never forwards a publish request. Its sparse index mirrors
crates.io for dependencies, so packages are prepared against the same index
Cargo uses for crates.io. The captured packages themselves list only uploads
from this run: once a release reaches crates.io, its mirrored entry would make
Cargo refuse to capture that version again for every later check.
"""

import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import re


ROOT = Path(__file__).resolve().parents[1]
MAX_UPLOAD = 32 * 1024 * 1024
MAX_INDEX = 4 * 1024 * 1024


def index_path(name):
    if len(name) == 1:
        return "1/" + name
    if len(name) == 2:
        return "2/" + name
    if len(name) == 3:
        return "3/" + name[0] + "/" + name
    return name[:2] + "/" + name[2:4] + "/" + name


def parse_upload(body, expected_name, version, expected_archive_sha):
    if len(body) < 8 or len(body) > MAX_UPLOAD:
        raise ValueError("invalid Cargo upload size")
    metadata_size = struct.unpack_from("<I", body)[0]
    if not 0 < metadata_size <= 128 * 1024 or len(body) < 8 + metadata_size:
        raise ValueError("invalid Cargo metadata size")
    metadata = json.loads(body[4 : 4 + metadata_size])
    archive_size = struct.unpack_from("<I", body, 4 + metadata_size)[0]
    if not 0 < archive_size <= MAX_UPLOAD or len(body) != 8 + metadata_size + archive_size:
        raise ValueError("invalid Cargo archive size")
    if metadata.get("name") != expected_name or metadata.get("vers") != version:
        raise ValueError("Cargo package identity differs from release")
    archive = body[8 + metadata_size :]
    if archive_size > 10 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != expected_archive_sha:
        raise ValueError("capture_archive_differs_from_verified_package")
    dependencies = [
        {
            "name": item.get("explicit_name_in_toml") or item["name"],
            "package": item["name"] if item.get("explicit_name_in_toml") else None,
            "req": item["version_req"],
            "features": item["features"],
            "optional": item["optional"],
            "default_features": item["default_features"],
            "target": item["target"],
            "kind": item["kind"],
            "registry": item["registry"],
        }
        for item in metadata["deps"]
    ]
    record = {
        "name": expected_name,
        "vers": version,
        "deps": dependencies,
        "cksum": hashlib.sha256(archive).hexdigest(),
        "features": metadata["features"],
        "yanked": False,
    }
    return (json.dumps(record, separators=(",", ":")) + "\n").encode()


class LocalRegistry(http.server.ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, output, version, expected_name, archive_sha):
        super().__init__(("127.0.0.1", 0), RegistryHandler)
        self.output = output
        self.version = version
        self.expected = expected_name
        self.archive_sha = archive_sha
        self.index = {}
        self.error = None
        self.token = secrets.token_hex(32)


class RegistryHandler(http.server.BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def do_GET(self):
        if self.path == "/index/config.json":
            data = json.dumps(
                {
                    "dl": f"http://127.0.0.1:{self.server.server_port}/crates",
                    "api": f"http://127.0.0.1:{self.server.server_port}",
                }
            ).encode()
        elif self.path.startswith("/crates/"):
            parts = self.path.split("/")
            if len(parts) != 5 or parts[4] != "download":
                self.send_error(404)
                return
            name, version = parts[2:4]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,127}", version):
                self.send_error(400)
                return
            asset = self.server.output / f"{name}-{version}.cargo-upload"
            if name == self.server.expected and version == self.server.version and asset.is_file():
                body = asset.read_bytes()
                offset = 8 + struct.unpack_from("<I", body)[0]
                data = body[offset:]
            else:
                try:
                    with urllib.request.urlopen(f"https://static.crates.io/crates/{name}/{name}-{version}.crate", timeout=20) as response:
                        data = response.read(MAX_UPLOAD + 1)
                except (OSError, TimeoutError):
                    self.send_error(503)
                    return
                if len(data) > MAX_UPLOAD:
                    self.send_error(413)
                    return
        elif self.path.startswith("/index/"):
            item = self.path.removeprefix("/index/")
            if not re.fullmatch(r"[A-Za-z0-9_/-]{1,128}", item) or any(part in ("", ".", "..") for part in item.split("/")):
                self.send_error(400)
                return
            data = self.server.index.get(item)
            if data is None and item == index_path(self.server.expected):
                self.send_error(404)
                return
            if data is None:
                try:
                    with urllib.request.urlopen(
                        "https://index.crates.io/" + item, timeout=20
                    ) as response:
                        data = response.read(MAX_INDEX + 1)
                except urllib.error.HTTPError as error:
                    self.send_error(error.code)
                    return
                except (OSError, TimeoutError):
                    self.send_error(503)
                    return
                if len(data) > MAX_INDEX:
                    self.send_error(413)
                    return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        if self.path != "/api/v1/crates/new" or not self.server.expected:
            self.send_error(404)
            return
        if not hmac.compare_digest(self.headers.get("Authorization", ""), self.server.token):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_UPLOAD:
                raise ValueError("invalid upload length")
            body = self.rfile.read(size)
            record = parse_upload(body, self.server.expected, self.server.version, self.server.archive_sha)
            self.server.output.joinpath(self.server.expected + "-" + self.server.version + ".cargo-upload").write_bytes(body)
            self.server.index[index_path(self.server.expected)] = record
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.server.error = "invalid_cargo_upload"
            self.send_error(422)
            return
        data = b'{"warnings":{"invalid_categories":[],"invalid_badges":[],"other":[]}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format, *_args):
        pass


def capture(name, version, archive, output, timeout):
    """Capture one independently versioned crate; never contact a registry to write."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", version):
        raise ValueError("invalid_package_identity")
    if not archive.is_file() or not 0 < archive.stat().st_size <= 10 * 1024 * 1024:
        raise ValueError("verified_package_missing_or_oversized")
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    server = LocalRegistry(output, version, name, expected)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        index = f"sparse+http://127.0.0.1:{server.server_port}/index/"
        environment = os.environ.copy()
        environment.pop("CARGO_REGISTRY_TOKEN", None)
        environment.pop("CARGO_REGISTRIES_CRATES_IO_TOKEN", None)
        environment["CARGO_REGISTRIES_RUNNERLESS_CAPTURE_INDEX"] = index
        environment["CARGO_REGISTRIES_RUNNERLESS_CAPTURE_TOKEN"] = server.token
        environment["CARGO_REGISTRIES_RUNNERLESS_CAPTURE_CREDENTIAL_PROVIDER"] = "cargo:token"
        command = ["cargo", "--config", 'source.crates-io.replace-with="local-mirror"',
                   "--config", f'source.local-mirror.registry="{index}"', "publish",
                   "--registry", "runnerless-capture", "--locked", "-p", name]
        result = subprocess.run(command, env=environment, timeout=timeout, check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        asset = output / f"{name}-{version}.cargo-upload"
        if result.returncode or not asset.is_file() or server.error:
            raise RuntimeError("cargo_capture_failed")
        parse_upload(asset.read_bytes(), name, version, expected)
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        output.joinpath("SHA256SUMS").write_text(f"{digest}  {asset.name}\n")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
