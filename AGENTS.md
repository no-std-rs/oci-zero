# Agent & contributor guide

Guidance for humans and AI agents working in this repository.

## Pull request titles

**PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/).**
This is not cosmetic: PRs are squash-merged, so the **PR title becomes the commit
message on `main`**, and Runnerless parses those commit
messages to decide version bumps and to generate each crate's changelog. A sloppy
title produces a wrong version bump and a bad changelog entry.

### Format

```
<type>[optional (scope)][optional !]: <description>
```

- Use the imperative mood, lower-case, no trailing period.
- Add `!` (or a `BREAKING CHANGE:` footer) for breaking changes.

### Types and how Runnerless treats them

| Type       | Meaning                                   | Version effect (pre-1.0) |
| ---------- | ----------------------------------------- | ------------------------ |
| `feat`     | New user-facing capability                | minor bump               |
| `fix`      | Bug fix                                    | patch bump               |
| `perf`     | Performance improvement                   | patch bump               |
| `refactor` | Behaviour-preserving code change          | no release, in changelog |
| `docs`     | Documentation only                        | no release, in changelog |
| `test`     | Tests only                                | no release               |
| `ci`       | CI / workflow changes                     | no release               |
| `build`    | Build system or dependency changes        | no release               |
| `chore`    | Maintenance not covered above             | no release               |

A `feat!:` / `fix!:` (or `BREAKING CHANGE:` footer) triggers a breaking bump.

This table is enforced by `.ci-toolkit.yml`: `release_commit_types` allows only
`feat`, `fix`, and `perf` to trigger ordinary releases. Changelog visibility is
independent, so docs/refactor appear in notes without triggering releases alone.
Breaking changes and explicit `Release-As` footers also trigger releases.

### Scopes

Prefer a crate name as the scope so changelogs attribute correctly:
`oci-zero`, `gzip-zero`, `zstd-zero`, `oci-zero-web`, `oci-zero-no-std-extract`.
Example: `fix(gzip-zero): reject trailer with wrong ISIZE`.

### Examples

```
feat(oci-zero): stream blob digests without buffering
fix(gzip-zero): reject members with a corrupt header CRC
perf(zstd-zero): avoid recomputing the FSE table per block
docs: document Runnerless releases in AGENTS.md
chore: bump miniz_oxide to 0.9.1
feat(oci-zero)!: rename Decoder::decode to Decoder::step
```

PR titles are enforced by `configuration.titlePolicy()` in `.runnerless-ci.ts`.

## Releasing

Releases are owned by the trusted `.runnerless-ci.ts` program:

1. The native `strategy: cargo` groups the independently versioned crates into one
   release PR, updating Cargo manifests, dependency requirements, Cargo.lock,
   version files and changelogs together. Dependents receive required version bumps.
2. Merging that PR causes Runnerless to create each changed crate's existing
   `<crate>-vX.Y.Z` tag and GitHub Release, in dependency order.
3. `publish-release.yml` verifies immutable release identity and ancestry, waits
   for dependency publication, and captures the selected crate against a loopback
   registry. It attaches the verified upload body and SHA256SUMS without a crates.io
   credential. Runnerless performs the registry write through `packages.crates`;
   matching immutable bytes reconcile on retry and conflicting bytes fail.

The native release PR keeps manifest and version-file versions consistent. No
repository preparation hook or generated dirty build inputs are needed. Private
workspace package versions remain unchanged. CI packages the public crates with
`--locked`. Enrollment and recovery are described in [RELEASING.md](RELEASING.md).

Do not run `cargo publish` by hand; let the pipeline do it.

## Crates

- `oci-zero` — no-std, allocation-free streaming core for OCI registries (published)
- `gzip-zero` — no-std streaming gzip decoder (published)
- `zstd-zero` — no-std streaming Zstandard decoder (published)
- `oci-zero-no-std-extract`, `oci-zero-web` — examples/tools, `publish = false`

The `oci-zero` release track owns the repository, including `tools/`, `web/` and
decoder changes; its generated version and changelog files are excluded. Use `ci:`,
`build:` or `chore:` for changes that are not user-visible library changes.
