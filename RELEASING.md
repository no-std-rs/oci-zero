# Releases

The trusted `.runnerless-ci.ts` program owns release proposals, tags and GitHub
Releases through `workflow().releasePlease()`. `.ci-toolkit.yml` supplies its policy;
it does not enable a second release writer. The existing complexity publication
and merged-PR artifact cleanup remain in the same program.

The three crates keep independent versions and their existing tag namespaces:

| Crate | Version authority | Changelog | Tag |
|---|---|---|---|
| oci-zero | `version.txt` | `CHANGELOG.md` | `oci-zero-vX.Y.Z` |
| gzip-zero | `gzip-zero/version.txt` | `gzip-zero/CHANGELOG.md` | `gzip-zero-vX.Y.Z` |
| zstd-zero | `zstd-zero/version.txt` | `zstd-zero/CHANGELOG.md` | `zstd-zero-vX.Y.Z` |

The initial versions match the last published versions. Existing tags are retained;
the migration does not publish a version or rewrite release history. The root track
claims repository changes, including decoder changes. Decoder tracks claim their
own directories. Generated version and changelog files are excluded from attribution.
Only `feat`, `fix` and `perf` trigger ordinary releases. Breaking changes and explicit
`Release-As` footers can also trigger a release. A breaking pre-1.0 change advances
the minor version. CI, build, maintenance and test commits do not cause releases.

## Release sequence

1. A main push reconciles Runnerless release PRs for the affected tracks.
2. Review and merge decoder release PRs before the dependent `oci-zero` release PR.
   Wait for the decoder versions to become available on crates.io.
3. Runnerless creates each merged release's tag and published GitHub Release.
4. `publish-release.yml` checks that the release is stable and its tagged commit is
   in main, prepares Cargo's build inputs, verifies the selected package, and uses
   crates.io OIDC to publish that crate. It does not plan versions or create releases.

The version files are authoritative. Runnerless currently writes plain versions,
so `python3 tools/prepare-release.py <crate>-vX.Y.Z` renders all three manifest
versions, internal dependency requirements and lock entries before packaging.
The tag must match the selected crate's version file. Private package versions
remain unchanged. These generated manifest/lock changes stay in the build checkout;
the release PR records the version in its version file. CI runs this same preparation
and verifies all three distributable packages together, including unpublished
workspace versions. It uses `--locked --allow-dirty` to permit only the deliberate
preparation changes while keeping dependency resolution locked.

The publisher verifies only the selected crate against the real registry. A root
release with an unavailable decoder dependency fails before obtaining a publishing
credential. After publishing the decoder, rerun the failed publish job or dispatch
`Publish Runnerless release` with the existing stable tag. A dispatch must name an
existing published release in main; it cannot publish arbitrary branch contents.

## Enrollment

Runnerless must have active organization-bound adoption for `no-std-rs/oci-zero`.
The repository already uses Runnerless for complexity artifacts; release authority
also requires that adoption to be active. No infrastructure allowlist is added here.

For **each of oci-zero, gzip-zero and zstd-zero**, update its crates.io Trusted
Publisher configuration to repository owner `no-std-rs`, repository `oci-zero`,
workflow filename `publish-release.yml`, with no environment name. The repository
transfer and workflow rename mean the previous publisher configuration must not be
assumed valid. Verify this before merging a generated release PR. Configuration is
an out-of-band prerequisite, not an action performed by this code change. See
[crates.io trusted publishing](https://crates.io/docs/trusted-publishing).

The publishing job uses a short-lived OIDC credential. No release GitHub App,
private key, durable crates.io token, new cloud resource or subscription is required.

## Runnerless gaps

- [my-infra#275](https://github.com/pawelchcki/my-infra/issues/275): native Cargo
  manifest/lock updates, dependent version planning and publication ordering.
  Until then, use the preparation hook and release decoders before their dependent.
- [my-infra#276](https://github.com/pawelchcki/my-infra/issues/276): Runnerless-owned
  crates.io OIDC publishing. Its current native Cargo relay requires API tokens;
  the small GitHub publish job preserves this repository's existing OIDC model.
- [my-infra#277](https://github.com/pawelchcki/my-infra/issues/277): separate release
  triggers from changelog visibility. Until then, docs/refactor entries are hidden
  to prevent those commits from causing patch releases. They were previously
  included in notes when another commit triggered a release.
