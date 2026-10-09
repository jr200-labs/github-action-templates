# Topic 562 Actions safeguards

Tracked before implementation: explicit opt-in to Scotty's fixed unsigned Xcode
contract, approved runtime and owned simulator, test diagnostic/parallelism
restrictions, execution and post-test deadlines, durable ownership recovery,
and no rewriting of arbitrary user commands. Keep the existing generic lane
available for consumers that have not opted in. Native and actual Mac acceptance
remain distinct from template/source validation. Installation of the companion
Scotty release and consumer adoption of the shared release are prerequisites.

The reusable Swift workflow now accepts `scotty-unattended: true` and
`scotty-platform: ios` or `macos`. This lane invokes installed Scotty's
`verify-action` in the already allocated job. It uses the resolved project and
scheme but never adopts a caller-provided simulator destination. Scotty owns the
runtime/device and private DerivedData, disables signing and test diagnostics,
serializes test simulators, bounds Xcode to 900 seconds and post-test completion
to 90 seconds, retains crash projections, and preserves ownership for recovery.
Missing/older Scotty fails visibly rather than falling back to unconstrained
Xcode. The ordinary generic lane remains available without the opt-in.

Template tests do not certify absence of dialogs on macOS 27. Adopt this lane
only after installing the companion Scotty release and recording actual Mac
host acceptance; iOS also requires a passing simulator probe for the exact
approved runtime build. Evidence must match the installed executor/OS and be
less than seven days old. This PR does not change consumer pins or generated caller files;
consumer adoption must follow the shared release and normal synchronization.

## Adoption review (2026-10-09)

The protected-lane implementation and result upload now travel in one branch.
Its two typed opt-in inputs must be declared before a consumer can call it.
Result bundles upload on every outcome with five-day retention, matching the
Actions storage policy; original native bundles remain restricted artifacts.
The Kanji consumer is manual-only and must merge after this shared contract.
Neither publication nor merging runs the protected workflow. Actual execution
remains blocked until fresh host and exact-runtime simulator acceptance passes.
