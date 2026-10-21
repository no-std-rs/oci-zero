#!/usr/bin/env python3
"""Render the three independent Runnerless versions into Cargo's build inputs."""

from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"oci-zero": ".", "gzip-zero": "gzip-zero", "zstd-zero": "zstd-zero"}
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def prepare(root, tag):
    match = re.fullmatch(r"(oci-zero|gzip-zero|zstd-zero)-v(" + VERSION + r")", tag)
    if not match:
        raise ValueError("expected a stable <crate>-vMAJOR.MINOR.PATCH tag")
    name, version = match.groups()
    versions = {}
    for package, directory in PACKAGES.items():
        value = (root / directory / "version.txt").read_text().strip()
        if not re.fullmatch(VERSION, value):
            raise ValueError(f"invalid release version for {package}")
        versions[package] = value
    if version != versions[name]:
        raise ValueError("tag differs from its crate's version.txt")

    updates = {}
    for package, directory in PACKAGES.items():
        path = root / directory / "Cargo.toml"
        source = path.read_text()
        if tomllib.loads(source)["package"]["name"] != package:
            raise ValueError(f"unexpected package identity in {path}")
        source, count = re.subn(r'(?m)^version\s*=\s*"[^"\n]+"',
                                f'version = "{versions[package]}"', source)
        if count != 1:
            raise ValueError(f"expected one package version in {path}")

        def dependency(match):
            dependency_name, body = match.groups()
            fields = tomllib.loads("value = {" + body + "}")["value"]
            target = fields.get("package", dependency_name)
            if target in PACKAGES and "path" in fields and "version" in fields:
                if (path.parent / fields["path"]).resolve() != (root / PACKAGES[target]).resolve():
                    raise ValueError(f"unexpected path for {target}")
                body = re.sub(r'version\s*=\s*"[^"\n]+"',
                              f'version = "{versions[target]}"', body)
            return dependency_name + " = {" + body + "}"

        source = re.sub(r'(?m)^([A-Za-z0-9_-]+)\s*=\s*\{([^\n]+)\}', dependency, source)
        tomllib.loads(source)
        updates[path] = source

    lock = root / "Cargo.lock"
    locked = lock.read_text()
    for package, value in versions.items():
        locked, count = re.subn(
            r'(\[\[package\]\]\nname = "' + re.escape(package) + r'"\n)version = "[^"\n]+"',
            lambda match: match[1] + f'version = "{value}"', locked,
        )
        if count != 1:
            raise ValueError(f"expected one {package} entry in Cargo.lock")
    tomllib.loads(locked)
    updates[lock] = locked
    # Validate the whole update before changing any build input.
    for path, source in updates.items():
        path.write_text(source)
    return name, version


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare-release.py <crate>-vMAJOR.MINOR.PATCH")
    try:
        package, version = prepare(ROOT, sys.argv[1])
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error)) from None
    print(f"package={package}\nversion={version}")
