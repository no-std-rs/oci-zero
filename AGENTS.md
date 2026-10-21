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
| `refactor` | Behaviour-preserving code change          | no release               |
| `docs`     | Documentation only                        | no release               |
| `test`     | Tests only                                | no release               |
| `ci`       | CI / workflow changes                     | no release               |
| `build`    | Build system or dependency changes        | no release               |
| `chore`    | Maintenance not covered above             | no release               |

A `feat!:` / `fix!:` (or `BREAKING CHANGE:` footer) triggers a breaking bump.

This table is enforced by `.ci-toolkit.yml`: only `feat`, `fix`, and `perf` are
visible release sections. Breaking changes and explicit `Release-As` footers also
trigger releases. Internal commit types stay hidden so a CI-only change does not
publish a new `oci-zero`. See [RELEASING.md](RELEASING.md) for the tracked platform gaps.

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

PR titles are enforced in CI by `.github/workflows/pr-title.yml`.

## Releasing

Releases are owned by the trusted `.runnerless-ci.ts` program:

1. Runnerless opens an independent release PR for each affected track in
   `.ci-toolkit.yml`, updating its `version.txt` and changelog.
2. Merging a release PR causes Runnerless to create its existing `<crate>-vX.Y.Z`
   tag and GitHub Release. Release dependent decoder crates before `oci-zero`.
3. `publish-release.yml` verifies the published release and main ancestry, renders
   Cargo manifests and the lockfile from the three independent version files, and
   publishes only that release's crate using crates.io OIDC trusted publishing.

The version files are the release authority. Run `tools/prepare-release.py` via
Python before packaging a release; CI checks the same preparation. Do not manually
change a Cargo version as a substitute for updating its version file. Private
workspace package versions remain unchanged. Enrollment and recovery are described
in [RELEASING.md](RELEASING.md).

Do not run `cargo publish` by hand; let the pipeline do it.

## Crates

- `oci-zero` — no-std, allocation-free streaming core for OCI registries (published)
- `gzip-zero` — no-std streaming gzip decoder (published)
- `zstd-zero` — no-std streaming Zstandard decoder (published)
- `oci-zero-no-std-extract`, `oci-zero-web` — examples/tools, `publish = false`

The `oci-zero` release track owns the repository, including `tools/`, `web/` and
decoder changes; its generated version and changelog files are excluded. Use `ci:`,
`build:` or `chore:` for changes that are not user-visible library changes.
