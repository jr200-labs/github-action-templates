# Cross-Org Reusable Workflow & GitHub App Gotchas

This repo (`jr200-labs/github-action-templates`) hosts reusable workflows
called by ~18 repos across three GitHub orgs: `whengas`, `jr200-labs`, and
`jr200`. Every gotcha below was discovered the hard way during the initial
setup (April 2026). GitHub's docs either don't cover these or bury them.

---

## 1. Org-level secrets don't cross org boundaries via `secrets: inherit`

`secrets: inherit` on a reusable workflow call **does not pass org-level
secrets across org boundaries**. A `whengas` repo calling a `jr200-labs`
reusable workflow will NOT inherit `whengas`'s org secrets.

Confusingly, `vars.*` (variables) DO cross orgs via the same mechanism —
only secrets are blocked.

**Workaround:** Declare the secret as a named input on the reusable
workflow's `workflow_call.secrets` block. The caller passes it explicitly:

```yaml
# Consumer wrapper (in whengas org)
secrets:
  INTEGRATION_APP_PRIVATE_KEY: ${{ secrets.INTEGRATION_APP_PRIVATE_KEY }}
```

Once you declare ANY named secret on `workflow_call.secrets`, only the
named ones flow through — you lose the magic inherit behaviour for that
workflow, even within the same org.

**Example:** `.github/workflows/renovate.yml` in this repo declares
`INTEGRATION_APP_PRIVATE_KEY` as a named optional secret for exactly
this reason.

---

## 2. GitHub Free plan blocks org-level secrets/variables on private repos

Org-level secrets and variables with `visibility: all` do NOT resolve at
workflow runtime for **private** repositories on the **GitHub Free plan**.
The API misleadingly reports `visibility: all` regardless of plan.

**Root cause:** GitHub Free only propagates org secrets/variables to
public repos. All `whengas` repos are private → org inheritance is
blocked. The `jr200-labs` org (also Free) doesn't have this problem
because its repos are public.

**Workaround:** Add per-repo duplicates of the variable + secret on
every private repo that needs them:

```bash
gh variable set INTEGRATION_APP_ID --repo whengas/<repo> --body "3216955"
gh secret set INTEGRATION_APP_PRIVATE_KEY --repo whengas/<repo> < key.pem
```

**Long-term fix:** Upgrade to GitHub Team ($4/user/month). Then delete
all per-repo duplicates — the org-level settings take effect immediately.

**Tracked in:** WG-42 (investigation), WG-44 (review upgrade decision).

---

## 3. `secrets.*` not allowed in `if:` expressions

You cannot reference `secrets.X` in a step-level or job-level `if:`
condition. GitHub Actions evaluates `if:` expressions before secrets are
bound to the step context.

**Workaround:** Pair every secret with a non-sensitive variable. Gate
on the variable:

```yaml
- name: Mint App installation token
  if: ${{ vars.INTEGRATION_APP_ID != '' }}
  uses: actions/create-github-app-token@v3
  with:
    app-id: ${{ vars.INTEGRATION_APP_ID }}
    private-key: ${{ secrets.INTEGRATION_APP_PRIVATE_KEY }}
```

---

## 4. Org-level variables don't resolve inside cross-org reusable workflows

Even when org variables are visible via the API, `vars.*` inside a
reusable workflow resolves from the **caller's** context. On private
repos (Free plan), the caller can't see org-level variables.

**Workaround:** Pass the variable from the caller's `with:` block:

```yaml
# Consumer wrapper
with:
  app-id: ${{ vars.INTEGRATION_APP_ID }}
```

The reusable workflow uses `inputs.app-id` instead of `vars.INTEGRATION_APP_ID`:

```yaml
if: ${{ inputs.app-id != '' || vars.INTEGRATION_APP_ID != '' }}
```

The fallback to `vars.*` keeps backward compat for callers that haven't
been updated. See the `app-id` input on `renovate.yml` in this repo.

---

## 5. App tokens bypass "Allow Actions to create PRs" setting

The org-level setting **"Allow GitHub Actions to create and approve pull
requests"** (Settings → Actions → General → Workflow permissions) only
controls `secrets.GITHUB_TOKEN`. App installation tokens minted via
`actions/create-github-app-token` **bypass this setting entirely**.

**Symptom if disabled:** Workflows using `GITHUB_TOKEN` fail with:
`GraphQL: GitHub Actions is not permitted to create or approve pull requests`

**Fix:** Enable at the org level, or use App tokens (which bypass it).

---

## 6. `workflows:write` permission needed for workflow file changes

Without the `workflows:write` permission on the GitHub App, pushes that
modify `.github/workflows/*.yml` are rejected — even for innocent
changes like bumping `actions/checkout` version.

Both `whengas-ci-integration` and `jr200-labs-cicd-bot` Apps have this
permission.

---

## 7. `secrets: inherit` is silent on failure

If a caller forgets `secrets: inherit` (or the explicit `secrets:` block),
the reusable workflow simply doesn't see any secrets. **No error, no
warning.** The App token mint step silently falls back to `GITHUB_TOKEN`.

**How to detect:** After wiring a new consumer, trigger a manual
`workflow_dispatch` and check the logs. Look for:
- "Mint App installation token" step: ✓ (ran) vs - (skipped)
- The `app-id:` line in the step's `with:` block: `***` (present) vs empty

---

## 8. `renovatebot/github-action` has no floating major tag

`@v44`, `@v46` don't exist — there's no floating major tag. Pin to a
specific patch version like `@v46.1.8`. Renovate itself can be bumped
independently via the action's `renovate-version` input (currently
defaults to `43`).

---

## 9. Renovate's zod schemas reject some valid YAML

`pnpm-workspace.yaml` containing `overrides: null` (perfectly valid
YAML and accepted by pnpm) crashes Renovate's npm extractor with a
zod validation error.

**Workaround:** Remove the `overrides: null` line.

---

## 10. Rolling back image tags is fragile — Renovate undoes it

If you roll back a deployment by pinning an older image tag in
`whengas-iac/values.yaml`, Renovate sees the newer (broken) tag as
"latest" and bumps it back on the next run.

**Correct approach:**
1. Publish a NEW version with the fix (so the broken version is no
   longer "latest")
2. Mark the broken version's GitHub Release as pre-release
   (`gh release edit <tag> --prerelease`) — Renovate skips pre-releases

**Safety net:** `whengas-iac/.githooks/pre-commit` automatically marks
skipped versions as pre-release when it detects a tag downgrade in
values.yaml. See WG-46.

---

## 11. `create-github-app-token@v3` is stricter about PEM format

After Renovate bumped `actions/create-github-app-token` from `@v2` to
`@v3`, some per-repo `INTEGRATION_APP_PRIVATE_KEY` secrets stopped
working with: `A JSON web token could not be decoded`.

**Fix:** Re-set the secret with a fresh `.pem` file:

```bash
gh secret set INTEGRATION_APP_PRIVATE_KEY --repo <repo> < key.pem
```

The `app-id` input is deprecated in v3 (replaced by `client-id`) but
still works. Will need updating eventually.

---

## 12. Caller top-level `permissions:` can silently break reusable workflows

When a caller workflow sets top-level `permissions:` that doesn't include
every scope the reusable workflow's jobs need, the whole run can fail with
`startup_failure` — **before any job logs are emitted**. The UI shows
0 jobs, duration ~1s, and the API returns no annotations.

Observed when first adopting `release_please.yaml` on a private repo:
the caller had `permissions: contents: read` at top level; the
reusable's release job declares `contents: write, pull-requests: write`.
Startup failed immediately.

Confusingly, the identical pattern works on some repos — suggesting the
cascade depends on org-level/repo-level workflow permission defaults
(`default_workflow_permissions`, `can_approve_pull_request_reviews`)
that differ silently between repos.

**Workaround:** Always declare the full set of scopes the reusable needs
at the caller's top-level `permissions:` block. For release-please:

```yaml
permissions:
  contents: write
  pull-requests: write
```

Also add `workflow_dispatch:` to the trigger list so you can retrigger
manually for diagnosis without needing a push to master.

**How to detect:** Watch the first post-merge workflow run. If it shows
`startup_failure` with 1s duration and no job logs, suspect permissions
before touching YAML syntax.

---

## Quick Reference: GitHub App Permissions

Both Apps (`whengas-ci-integration` and `jr200-labs-cicd-bot`) have:

| Permission | Why |
|---|---|
| `actions: read` | Read workflow runs |
| `contents: write` | Push commits, create branches |
| `issues: write` | Renovate Dependency Dashboard |
| `metadata: read` | Required for all Apps |
| `packages: read` | Access ghcr.io private packages |
| `pull_requests: write` | Create/update Renovate PRs |
| `statuses: read` | Read commit statuses (integration gate) |
| `workflows: write` | Push changes to `.github/workflows/` |

Key rotation is tracked in JRL-18 (due 2027-04-11).
