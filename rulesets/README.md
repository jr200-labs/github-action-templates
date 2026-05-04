# Rulesets

Source-of-truth for GitHub branch-protection rulesets across jr200-labs and consumer orgs. Reconciled by `scripts/apply-rulesets.sh`.

## Layout

```
rulesets/
├── trunk-protect.json   # canonical ruleset body (verbatim API payload)
├── targets.yaml         # which ruleset → which org → org-scope or per-repo-scope
└── README.md            # this file
```

## Run

```bash
scripts/apply-rulesets.sh             # reconcile
scripts/apply-rulesets.sh --dry-run   # show what would change
scripts/apply-rulesets.sh --org jr200-labs --dry-run
scripts/apply-rulesets.sh --repo jr200-labs/mem0-dashboard --ruleset trunk-protect
```

Requires `gh`, `jq`, `yq` and a `gh auth login` with admin on every targeted org. `gh` token plan must support requested scope — org-level rulesets need GitHub Team; free orgs fall back to per-repo.

## What `trunk-protect` does

- Targets default branch (`~DEFAULT_BRANCH`) of every covered repo.
- `pull_request` rule: 1 approving review, dismiss stale on push, require CODEOWNERS review.
- `required_linear_history`: aligns with squash-only merge policy.
- `non_fast_forward`: blocks force-push.
- `deletion`: blocks branch deletion.
- `bypass_actors: []` — no admin override.

Adjacent repo settings are also reconciled on every targeted repo:

- `allow_auto_merge=false` so PRs can't auto-merge ahead of the CODEOWNERS review.
- `delete_branch_on_merge=true` so merged PR branches are cleaned up automatically.

## Adding a target

Edit `targets.yaml`:

```yaml
trunk-protect:
  whengas: org
  jr200-labs: repo
  some-new-org: org    # add this line
```

Then `scripts/apply-rulesets.sh`. Idempotent — existing rulesets get PUT in place; new ones get POSTed.

## Onboarding a new repository

Use the repo filter to apply existing canonical rulesets to one new repo without touching the rest of an org:

```bash
scripts/apply-rulesets.sh --repo jr200-labs/new-repo --ruleset trunk-protect
```

Useful flags for staged rollout:

- `--repo ORG/REPO` (repeatable): target specific repos only.
- `--org ORG`: narrow to one org from `targets.yaml`.
- `--ruleset NAME`: apply one canonical ruleset only.
- `--skip-auto-merge`: skip repo-level merge-setting patches (`allow_auto_merge=false`, `delete_branch_on_merge=true`) if you only want ruleset reconciliation.

## Adding a new ruleset

1. Land the body at `rulesets/<name>.json` (verbatim GitHub API payload).
2. Add a section under `<name>:` in `targets.yaml` listing each org's scope.
3. Run `scripts/apply-rulesets.sh`.

## Drift

The script is reconcile-only — running it brings live state to match canonical. To detect drift on a schedule, wrap it in a workflow that runs `--dry-run` and opens a tracking issue if any line shows `DRY:`. (Not yet implemented; same pattern as `drift_check_merge_settings.yaml`.)
