#!/usr/bin/env python3
"""Prepare immutable Cargo upload assets for the Runnerless registry publisher.

GitHub Actions verifies and captures bytes without a crates.io credential. Only
Runnerless may perform the registry write; this helper attaches GitHub assets.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import tomllib
import urllib.parse

STABLE = r"(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
CRATE = r"[A-Za-z][A-Za-z0-9_-]{0,63}"
DEADLINE = None


def remaining(limit, deadline=None, timeout_reason="release_deadline_exhausted_retry_same_revision"):
    now = time.time()
    if DEADLINE is not None and DEADLINE <= now:
        raise RuntimeError("release_deadline_exhausted_retry_same_revision")
    if deadline is not None and deadline <= now:
        raise RuntimeError(timeout_reason)
    return min([limit] + [value - now for value in [DEADLINE, deadline] if value is not None])


def run(*args):
    return subprocess.check_output(args, text=True, timeout=remaining(180)).strip()


def require_crates_io(package):
    destinations = package.get("publish")
    if destinations is not None and (not isinstance(destinations, list) or "crates-io" not in destinations):
        raise ValueError("manifest_disallows_crates_io")


def release_revision(repository, tag):
    commit = json.loads(run("gh", "api", "repos/" + repository + "/commits/" + urllib.parse.quote("refs/tags/" + tag, safe="")))
    revision = commit.get("sha")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("release_revision_unavailable")
    return revision


def select(repository, tag, allowed):
    if repository != os.environ.get("GITHUB_REPOSITORY") or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        raise ValueError("repository_identity_mismatch")
    chosen = [(name, tag[len(name) + 2:]) for name in allowed
              if re.fullmatch(CRATE, name) and tag.startswith(name + "-v")
              and re.fullmatch(STABLE, tag[len(name) + 2:])]
    if len(chosen) != 1:
        raise ValueError("stable_configured_crate_tag_required")
    name, version = chosen[0]
    base = "repos/" + repository
    release = json.loads(run("gh", "api", base + "/releases/tags/" + tag))
    if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("stable_published_release_required")
    revision = run("git", "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or run("git", "rev-parse", "refs/tags/" + tag + "^{commit}") != revision:
        raise ValueError("release_revision_mismatch")
    if release_revision(repository, tag) != revision:
        raise ValueError("remote_release_tag_changed")
    facts = json.loads(run("gh", "api", base))
    branch = facts["default_branch"]
    # GitHub API reads use GH_TOKEN without persisting Git credentials. This also
    # supports private repositories with checkout persist-credentials: false.
    tip = json.loads(run("gh", "api", base + "/commits/" + urllib.parse.quote("refs/heads/" + branch, safe=""))).get("sha")
    if not isinstance(tip, str) or not re.fullmatch(r"[0-9a-f]{40}", tip):
        raise ValueError("default_branch_revision_unavailable")
    relation = json.loads(run("gh", "api", base + "/compare/" + revision + "..." + tip))
    if relation.get("status") not in ("ahead", "identical"):
        raise ValueError("release_not_in_default_history")
    if run("git", "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("release_checkout_dirty")
    metadata = json.loads(run("cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"))
    matches = [p for p in metadata["packages"] if p["name"] == name and p["version"] == version and p["id"] in metadata["workspace_members"]]
    if len(matches) != 1:
        raise ValueError("manifest_release_identity_mismatch")
    require_crates_io(matches[0])
    return name, version, matches[0], metadata


@contextmanager
def request_deadline(budget):
    # This delegated job runs on Ubuntu. A wall timer covers DNS, TLS, response
    # headers and body, including peers that continuously dribble bytes.
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):
        budget()  # Distinguish registry, dependency and shared deadline expiry.
        raise RuntimeError("registry_index_unavailable")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, budget())
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])


def index(name, version, deadline=None, timeout_reason="registry_index_unavailable"):
    if not re.fullmatch(CRATE, name) or not re.fullmatch(STABLE, version):
        raise ValueError("invalid_package_identity")
    index_name = name.lower()
    path = ("1/" + index_name if len(name) == 1 else "2/" + index_name if len(name) == 2
            else "3/" + index_name[0] + "/" + index_name if len(name) == 3
            else index_name[:2] + "/" + index_name[2:4] + "/" + index_name)
    request_until = time.time() + remaining(30, deadline, timeout_reason)
    def budget():
        return min(remaining(30, deadline, timeout_reason), remaining(30, request_until, "registry_index_unavailable"))
    try:
        with request_deadline(budget), closing(http.client.HTTPSConnection("index.crates.io", timeout=budget())) as connection:
            connection.request("GET", "/" + path, headers={"User-Agent": "runnerless-cargo-publisher/1"})
            # Keep the actual socket even when getresponse detaches a connection
            # marked close. Shrink its timeout before each bounded read.
            socket = connection.sock
            socket.settimeout(budget())
            with connection.getresponse() as response:
                if response.status == 404:
                    return None
                if response.status != 200:
                    raise RuntimeError("registry_index_unavailable")
                body = bytearray()
                while len(body) <= 16 * 1024 * 1024:
                    socket.settimeout(budget())
                    chunk = response.read1(min(64 * 1024, 16 * 1024 * 1024 + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
    except (OSError, http.client.HTTPException):
        budget()  # Preserve the actual local/shared deadline reason if it expired.
        raise RuntimeError("registry_index_unavailable") from None
    if len(body) > 16 * 1024 * 1024:
        raise ValueError("registry_index_limit")
    matches = [json.loads(line) for line in body.splitlines() if line]
    matches = [entry for entry in matches if entry.get("vers") == version]
    if len(matches) > 1 or matches and (matches[0].get("name") != name or not re.fullmatch(r"[0-9a-f]{64}", matches[0].get("cksum", ""))):
        raise ValueError("registry_index_invalid")
    return matches[0] if matches else None


def reconcile(name, version, archive, deadline=None, timeout_reason="registry_index_unavailable"):
    entry = index(name, version, deadline=deadline, timeout_reason=timeout_reason)
    if entry is None:
        return False
    if archive.stat().st_size > 10 * 1024 * 1024:
        raise ValueError("crate_size_limit")
    if entry["cksum"] != hashlib.sha256(archive.read_bytes()).hexdigest():
        raise ValueError("immutable_registry_digest_conflict")
    if entry.get("yanked") is not False:
        raise ValueError("registry_version_yanked")
    return True


def wait_dependencies(package, metadata, visited=None):
    # Target-specific, optional and renamed path dependencies all appear here.
    visited = set() if visited is None else visited
    members = {str(Path(p["manifest_path"]).parent): p for p in metadata["packages"]}
    for dep in package["dependencies"]:
        if dep.get("path") is None or dep.get("kind") == "dev":
            continue
        target = members.get(str(Path(dep["path"])))
        if not target:
            raise ValueError("unresolved_workspace_dependency")
        require_crates_io(target)
        if target["name"] in visited:
            continue
        visited.add(target["name"])
        wait_dependencies(target, metadata, visited)
        tag = target["name"] + "-v" + target["version"]
        revision = run("git", "rev-parse", "refs/tags/" + tag + "^{commit}")
        if release_revision(os.environ["GITHUB_REPOSITORY"], tag) != revision:
            raise ValueError("remote_dependency_tag_changed")
        head = run("git", "rev-parse", "HEAD")
        subprocess.run(["git", "merge-base", "--is-ancestor", revision, head], check=True, timeout=remaining(30))
        relative = Path(target["manifest_path"]).relative_to(Path(metadata["workspace_root"]))
        original = tomllib.loads(run("git", "show", revision + ":" + relative.as_posix()))
        if original.get("package", {}).get("name") != target["name"] or original["package"].get("version") != target["version"]:
            raise ValueError("dependency_tag_manifest_mismatch")
        archive = None
        if revision == head:
            # A member changed in this atomic release must match these exact bytes,
            # not merely occupy the version that the dependent is waiting on.
            subprocess.run(["cargo", "package", "--locked", "--registry", "crates-io", "-p", target["name"]], check=True, timeout=remaining(600))
            archive = Path(metadata["target_directory"]) / "package" / (target["name"] + "-" + target["version"] + ".crate")
        wait_until = time.time() + 300
        for attempt in range(31):
            if archive is not None:
                ready = reconcile(target["name"], target["version"], archive, deadline=wait_until, timeout_reason="dependency_not_visible_retry_after_dependency_release")
            else:
                # Unchanged historical members keep their original release bytes;
                # packaging them at HEAD would change .cargo_vcs_info.json.
                entry = index(target["name"], target["version"], deadline=wait_until, timeout_reason="dependency_not_visible_retry_after_dependency_release")
                ready = entry is not None and entry.get("yanked") is False
            if ready:
                break
            if attempt == 30:
                raise RuntimeError("dependency_not_visible_retry_after_dependency_release")
            time.sleep(remaining(10, wait_until, "dependency_not_visible_retry_after_dependency_release"))


def attach(repository, tag, name, version, directory):
    """Reconcile immutable GitHub assets before any upload; never publish to crates.io."""
    files = [Path(directory) / f"{name}-{version}.cargo-upload", Path(directory) / "SHA256SUMS"]
    if any(not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024 for path in files):
        raise ValueError("prepared_release_asset_missing_or_oversized")
    expected = hashlib.sha256(files[0].read_bytes()).hexdigest()
    if files[1].read_text() != f"{expected}  {files[0].name}\n":
        raise ValueError("prepared_release_checksums_mismatch")
    release = json.loads(run("gh", "api", "repos/" + repository + "/releases/tags/" + tag))
    if release.get("tag_name") != tag or release.get("draft") is not False or release.get("prerelease") is not False or not release.get("published_at"):
        raise ValueError("stable_published_release_required")
    existing = {}
    for asset in release.get("assets", []):
        if asset["name"] in existing:
            raise ValueError("duplicate_release_asset")
        existing[asset["name"]] = asset
    missing = []
    for path in files:
        asset = existing.get(path.name)
        if asset is None:
            missing.append(str(path))
            continue
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if asset.get("size") != path.stat().st_size:
            raise ValueError("immutable_release_asset_conflict")
        observed = asset.get("digest")
        if observed is None:
            retained = subprocess.check_output(["gh", "api", f"repos/{repository}/releases/assets/{asset['id']}", "--header", "Accept: application/octet-stream"], timeout=remaining(180))
            observed = "sha256:" + hashlib.sha256(retained).hexdigest()
        if observed != digest:
            raise ValueError("immutable_release_asset_conflict")
    if missing:
        subprocess.run(["gh", "release", "upload", tag, *missing, "--repo", repository], check=True, timeout=remaining(180))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["package", "capture", "attach"])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--crate", action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.phase != "package" and args.output is None:
        parser.error("capture and attach require --output")
    global DEADLINE
    now = time.time()
    DEADLINE = float(os.environ.get("RUNNERLESS_PUBLISH_DEADLINE", now + 18 * 60))
    if not math.isfinite(DEADLINE) or DEADLINE > now + 18 * 60:
        raise ValueError("invalid_release_deadline")
    remaining(1)
    name, version, package, metadata = select(args.repository, args.tag, args.crate)
    archive = Path(metadata["target_directory"]) / "package" / f"{name}-{version}.crate"
    if args.phase == "package":
        wait_dependencies(package, metadata)
        subprocess.run(["cargo", "package", "--locked", "--registry", "crates-io", "-p", name], check=True, timeout=remaining(600))
        publish = not reconcile(name, version, archive)
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            print("publish=" + str(publish).lower(), file=output)
            print("deadline=" + str(DEADLINE), file=output)
    elif args.phase == "capture":
        import importlib.util
        spec = importlib.util.spec_from_file_location("cargo_capture", Path(__file__).with_name("runnerless-cargo-capture.py"))
        capture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(capture)
        capture.capture(name, version, archive, args.output, remaining(600))
    else:
        attach(args.repository, args.tag, name, version, args.output)



if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # Keep errors fixed and omit subprocess output, URLs, or environment values.
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else "release_verification_or_cargo_failed"
        raise SystemExit(message) from None
