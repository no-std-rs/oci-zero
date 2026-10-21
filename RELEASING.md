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
packages in dependency order. Independently triggered `publish-release.yml` jobs
use `tools/runnerless-cargo-publish.py`, copied from the supported my-infra recipe
at `335d35f2`. The job:

1. Verifies the repository, allowlisted stable crate tag, exact local and remote
   tag revision, published release, current default-branch ancestry and Cargo identity.
2. Waits for local dependencies on crates.io. Dependencies tagged at the same
   revision must match the archive digest produced by the pinned toolchain.
3. Packages with Cargo **1.95.0**, `--locked` and explicit registry `crates-io`.
4. Skips credential exchange/upload if the existing immutable version has matching
   bytes; otherwise exchanges GitHub OIDC and publishes the selected crate.
5. Reconciles a possibly interrupted upload against the sparse-index digest.
   Conflicting immutable bytes are never overwritten.

Dependency waits are bounded to five minutes each; upload reconciliation to two
minutes. Both phases share an 18-minute deadline inside a 20-minute job. A retry
starts a fresh budget and reconciles already-published bytes first. Retry the exact
tag with the same pinned toolchain. Do not edit the toolchain or release source
between retries of an already-published version.

## Enrollment and recovery

Runnerless adoption must remain active for `no-std-rs/oci-zero`. For **each of
oci-zero, gzip-zero and zstd-zero**, crates.io's Trusted Publisher must specify
owner `no-std-rs`, repository `oci-zero`, workflow `publish-release.yml`, and **no
environment name**. This migration preserves that existing workflow identity; it
does not provision or verify external publisher settings. Verify enrollment before
merging a generated release PR. No durable registry token, release App or new
GitHub environment is introduced.

To replay an interrupted existing stable release, dispatch `Publish Runnerless
Cargo release` with its exact tag. A dependency visibility failure means checking
its tag's job before retrying the dependent. An immutable digest conflict requires
investigating source/toolchain differences and choosing a new version; an unavailable
registry is never treated as a missing version.

Platform source support and successful self-deployment were verified in
[my-infra#283](https://github.com/pawelchcki/my-infra/pull/283).
[my-infra#277](https://github.com/pawelchcki/my-infra/issues/277) is closed.
[my-infra#275](https://github.com/pawelchcki/my-infra/issues/275) and
[my-infra#276](https://github.com/pawelchcki/my-infra/issues/276) retain the separate
actual three-crate registry-release acceptance. Adopting this strategy does not
claim that a registry release or publisher enrollment has occurred.
