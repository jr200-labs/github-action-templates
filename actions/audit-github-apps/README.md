# GitHub App inventory audit

This composite action compares an organization's installed Apps with JSON desired
state owned by the calling repository. Pin the action to an immutable commit.
Pass `inventory` (a checkout-relative path), `organization`, and `GH_TOKEN` in
the step environment. The token must grant organization Administration read.
The action does not modify registrations or installations.

Use `validate-only: 'true'` on pull requests to validate configuration without
credentials or API calls. Run the live audit on a schedule, workflow dispatch,
and after inventory changes merge. Mint the token with
`actions/create-github-app-token`, the calling organization's own credentials,
and `permission-organization-administration: read`.

```json
{
  "version": 1,
  "organization": "example-org",
  "apps": [{
    "slug": "example-audit",
    "app_id": 123,
    "repository_selection": "all",
    "permissions": {"metadata": "read", "checks": "read"},
    "events": []
  }]
}
```

For custom Apps, declare `permissions` and `events` to compare exact grants,
including excess permissions. For third-party Apps, omit both fields to audit
only identity, installation availability, and repository selection. Optional
`device_flow_required: true` with `client_id` verifies Device Flow without
printing issued codes. Missing, suspended, or drifted declared installations
fail the action; unrelated installations are outside this inventory's scope.
An empty inventory or a mismatched organization fails validation.
