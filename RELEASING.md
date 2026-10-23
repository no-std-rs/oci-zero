# Native Cargo releases

The trusted `.runnerless-ci.ts` program calls `workflow().releasePlease()`.
`.ci-toolkit.yml` declares `strategy: cargo` for the three public packages, each
with its own version, changelog and existing tag namespace. Every track lists the
same complete workspace manifest set, including private members, and Cargo.lock.

| Crate | Manifest | Version file | Tag |
|---|---|---|---|
| oci-zero | `Cargo.toml` | `version.txt` | `oci-zero-vX.Y.Z` |
| gzip-zero | `gzip-zero/Cargo.toml` | `gzip-zero/version.txt` | `gzip-zero-vX.Y.Z` |
| zstd-zero | `zstd-zero/Cargo.toml` | `zstd-zero/version.txt` | `zstd-zero-vX.Y.Z` |

Runnerless opens one atomic release PR on
`release-please--branches--main--tracks--cargo-workspace`. It updates selected
package versions, dependent constraints and required patch bumps, lockfile entries,
version files and per-package changelogs. Private package versions stay unchanged.
The Rust core serializes changed TOML documents, so their comments may be removed.
Manifest/version-file disagreements and unsupported workspace shapes fail before
writes. The former repository preparation hook has been removed: generated release
PRs must pass ordinary locked packaging from their committed source.

The root track retains its repository-wide ownership, including decoder changes;
decoder tracks claim their own directories. Generated version/changelog files are
excluded. Ordinary releases require `feat`, `fix` or `perf`; docs/refactor can appear
in notes without triggering a release. Breaking changes and `Release-As` retain
their release semantics. Breaking pre-1.0 changes advance the minor version.

After a release PR merges, Runnerless creates tags and GitHub Releases for changed
packages in dependency order. `publish-release.yml` prepares one selected crate:

1. Checks out preparation tools from the workflow's pinned revision, then checks
   out the immutable release tag. This also lets current tools prepare an older
   release without modifying its source or tag.
2. Verifies the repository, stable allowlisted crate tag, local/remote revision,
   published release, current default-branch ancestry, clean checkout and Cargo
   identity. Dependencies must already be visible on crates.io; same-revision
   dependencies must match the pinned-toolchain archive digest.
3. Packages with Cargo **1.95.0**, `--locked` and registry `crates-io`.
4. Captures Cargo's upload body against a loopback registry that never forwards
   writes. The embedded archive must match the verified package byte for byte.
   Only the selected crate is hidden in the local sparse index; independently
   versioned dependencies retain their published versions.
5. Attaches `<crate>-<version>.cargo-upload` and `SHA256SUMS`. Existing assets are
   checked before any write. Matching bytes are reused; conflicts are refused.

The preparation job has GitHub release-asset write permission, no OIDC permission,
and no crates.io credential. **Runnerless alone publishes to crates.io.** Its
trusted program stages `packages.crates` only after a successful preparation run
whose immutable revision matches the published release. Separate operations retain
each crate's version/tag, and core publication depends on any decoder operations
staged in the same delivery. Completion checks require actual success and
`failed: false`; build completion alone does not establish publication.

Dependency waits are bounded to five minutes each. The preparation phases share an
18-minute deadline inside a 20-minute job. Retries use the same pinned toolchain
and immutable release source. No manifest rewrite or `--allow-dirty` is used.

## Configure the Runnerless destination

Keep `no-std-rs/oci-zero` adopted in Runnerless. In its **Package publishing** panel,
add a **crates.io** destination containing only `oci-zero`, `gzip-zero` and
`zstd-zero`. Use the following exact package tag prefixes:

| Package | Tag prefix |
|---|---|
| oci-zero | `oci-zero-v` |
| gzip-zero | `gzip-zero-v` |
| zstd-zero | `zstd-zero-v` |

Select a repository token or an explicitly configured organization token that can
publish these packages, store it through Runnerless's write-only credential form,
and enable Production. Never put credentials in chat, workflow configuration,
arguments, release assets or source. This pipeline does not use crates.io's GitHub
Trusted Publisher enrollment. Destination readiness and actual registry acceptance
must be checked separately from source/CI readiness.

## Recover an existing release

For a normal release whose tag contains this workflow, dispatch **Prepare Cargo
release for Runnerless** at that same tag with the same `tag` input. A successful
completion hands publication to Runnerless after the assets are attached.

For the already-created `oci-zero-v0.3.0` (revision
`b2d990a4ad0d553b1e7f4c34d4b4ee09760ae217`), dispatch the current preparation
workflow from **main**, selecting that existing tag. It retains current tools but
packages the original tag's clean source. After its upload assets are verified,
re-run the existing successful **CI** workflow at that exact release revision to
hand off to the new trusted Runnerless program. The recovery branch accepts only
successful default-branch CI for an older revision whose tag still resolves to
that exact head and belongs to current default-branch history. Ordinary current-main
CI is not an asset-completion signal. Do not rerun the earlier direct-publisher
workflow attempt; it retains the old workflow definition.

A dependency visibility failure requires checking its own tag's preparation and
Runnerless publication before retrying the dependent. An immutable digest conflict
requires investigation; no existing release, tag or registry version is overwritten.
Only crates whose versions actually changed are released. This root release keeps
`gzip-zero` 0.1.1 and `zstd-zero` 0.2.1 unchanged.

[my-infra#275](https://github.com/pawelchcki/my-infra/issues/275) and
[my-infra#276](https://github.com/pawelchcki/my-infra/issues/276) retain separate
workspace/OIDC acceptance requirements. This Runnerless-only publication choice
does not claim that an OIDC exchange occurred or that all three crate versions
changed. Actual destination configuration and publication remain operational
prerequisites until verified.
