# github-action-templates

Reusable GitHub Actions workflows _and_ canonical caller workflows shared across consumer orgs, including native Swift/Xcode macOS app CI.

## Two halves

- **`.github/workflows/`** — *reusable* workflows (`workflow_call`). Each implements one piece of CI/release machinery. Consumer repos call these via `uses:`.
- **`consumers/`** — *caller* workflows. Each consuming repo declares groups in `.github/.shared-config.yaml`; `scripts/sync-shared` copies the matching caller files verbatim into `.github/workflows/`. A `drift-check` job in the `hygiene` group fails CI on any divergence. **Don't hand-author caller workflows.** See [AGENTS.md](AGENTS.md) for the full pattern.

Consumer repos should pin `.github/.shared-config.yaml` to a `shared-vX.Y.Z`
tag via `ref:`. Renovate tracks that pin and opens dedicated update PRs, so
shared workflow drift does not ride along with unrelated feature changes.
GAT releases the next `shared-vX.Y.Z` tag automatically when canonical
consumer workflows or shared files change on `master`, including a GitHub
Release with generated notes.

The single `macos-app` workflow validates ordinary code PRs and publishes releases.
Release Please version-bump PRs skip packaging; the release dispatch builds and
tests the tagged source once, then signs the appcast and uploads release assets
from that same job. Manual runs publish when `publish-release` is true.
Shared sync removes the retired `publish-macos-app` caller on upgrade.

Repositories may opt into [verified release archive reuse](docs/macos-release-reuse.md).
An exact match promotes the original full-verified ZIP on publication retries;
PR validation stays fresh, and changed source or toolchain inputs require full
verification. Archives stay on the Mac runner, with bounded local retention;
release builds do not upload them to the Actions cache service.

## Merge Policy

Consumer organizations can enforce default-branch protection centrally: rulesets require PRs, repo settings disable auto-merge by default + enable branch auto-delete, and the `lint-no-auto-merge` workflow in `hygiene` fails CI if any caller workflow invokes `gh pr merge`, `--auto-merge`, or `gh pr review --approve`. Shared-ref auto-merge guardrails live in config for a future opt-in, but the feature is currently disabled.

## Important

**Read [GOTCHAS.md](GOTCHAS.md) before wiring up a new consumer.** It
covers cross-org secrets, GitHub Free plan limitations, App token quirks,
and other issues that are poorly documented upstream.

## References

- [AGENTS.md](AGENTS.md) — Consumer pattern, group catalogue, how to add new groups
- [GOTCHAS.md](GOTCHAS.md) — Cross-org reusable workflow & GitHub App gotchas
- [GitHub: Reusing Workflows](https://docs.github.com/en/actions/sharing-automations/reusing-workflows)
- [GitHub: Accessing Workflows](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#allowing-access-to-components-in-a-private-repository)

Artifact retention is capped at five days. The universal runner-policy check also scans every consumer workflow and local composite action, including bespoke workflows, and rejects implicit or longer artifact retention and uncapped Buildx records. The `hygiene` group installs scheduled cleanup of artifacts older than five days from completed runs; manual runs default to a dry run. GitHub organization/repository retention settings must also be reconciled: workflow changes do not alter existing artifacts or erase already accrued storage billing. Release assets belong in releases rather than PR workflow artifacts.

The `release` group installs daily release-asset cleanup. Superseded assets expire after 14 days; the current release, the newest stable and prerelease for each versioned component, drafts, immutable releases, and explicitly protected tags are preserved. Release records and Git tags remain. Add versions still needed by deployments or other projects to `.github/release-asset-retention.json`, for example `{"protected_tags": ["v1.2.3"]}`. Unknown non-versioned tag families are preserved conservatively. Manual runs default to dry run. This release-asset cleanup is separate from Actions storage billing and its five-day artifact ceiling.

Both cleanup scripts stream timestamped progress to stderr immediately: inventory pages, candidate names/IDs/bytes, preservation decisions, deletions and verification. Pending API requests report a heartbeat every ten seconds and use a 30-second socket timeout. The final JSON receipt remains on stdout, so `> receipt.json` captures the result while progress stays visible. Tokens and request headers are never logged.

Manual `--apply` prints the exact deletion plan (IDs, names, release tags where applicable, sizes and total bytes) and requires typing `yes` before any deletion. Declining or EOF cancels with a JSON receipt and zero deletions. Noninteractive apply fails unless `--yes` is explicitly supplied. Automated workflows use `--apply --yes`; dry runs never prompt. The confirmed candidate inventory remains fixed and is revalidated before deletion.

Authentication uses `GH_TOKEN`, then `GITHUB_TOKEN`. Interactive manual runs without either token can reuse the existing local GitHub CLI login for `github.com`; credentials stay in memory and CLI output is captured without logging it. CI/noninteractive runs require an explicit environment token and never consult the local login. SSH Git keys alone cannot authorize REST API deletion. Cleanup does not log in, upload SSH keys, read private key files, or change accounts automatically.

To rank live Actions storage by repository, run `python3 scripts/report-actions-storage.py --owner whengas` from this templates checkout. It uses the same interactive authentication, paginates organization repositories and their artifacts directly, and prints live counts, MiB and bytes older than five days. It only makes GET requests. Add `--json` for a machine-readable report. Unreadable repositories are reported and produce a nonzero exit status; they are excluded from verified totals. These are current artifact bytes, not accrued billing, release assets, caches or package storage.

Publication-triggered Renovate runs filter by exact dependency name (`depName`)
while preserving the enabled/disabled policy from presets and repository rules.
They disable unrelated dependencies without re-enabling alternate scanners or
dependencies deliberately disabled by a consumer.

## Image source metadata

The Docker publisher supplies `SOURCE_REVISION` and `SOURCE_DIRTY` build arguments
from the actual checked-out Git tree, for both architectures. These reserved
arguments override caller-supplied values. They identify source, not the workflow
trigger commit; release and manual checkout refs can differ from that commit.
Dockerfiles may opt in with matching `ARG` declarations and embed the values in
their application's build metadata. Missing metadata must remain unknown.
No new labels, credentials or publication permissions are required.

## Docker CI

Opt into `docker-ci` for production-image validation on pull requests. Sync the
released group to install `ci-docker.yaml` and `.shared/check-docker-images.py`.
The canonical caller uses the shared `ci_docker.yaml` reusable with contents-read
permission and the configured default runner. It builds locally without registry
login, publication or artifact uploads. The ten-minute job limit includes smoke
tests. Docker, Python 3 and yq must be installed on the runner.

The verifier uses `.github/images.yaml`, the same catalog as publication. Without
a catalog, it builds `Dockerfile` or `docker/Dockerfile` with the repository root
as context. Catalog entries support `dockerfile`, `context`, `build_contexts`
(newline-delimited named contexts), and an optional `smoke-test` argv array:

```yaml
images:
  - name: example-service
    dockerfile: Dockerfile
    context: .
    smoke-test: [./scripts/smoke-image]
```

GAT builds each image with a unique local tag, appends that tag as the final
argument to the smoke command, and removes the image on success or failure.
Smoke commands execute directly without shell interpolation; consumer scripts
own application-specific assertions and must remove containers they start.
Build or smoke failures fail the check. The catalog is validated before any build.
Run `python3 .shared/check-docker-images.py` locally for the same check. Private
build-time registry authentication and cross-platform execution remain in the
release publisher; this PR lane validates the runner's native platform.

Add generic build capabilities here and release them before adopting them in a
consumer. A missing shared Docker check is a reason to extend GAT; application
assertions belong in repository scripts called by the shared workflow.
