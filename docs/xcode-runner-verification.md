# Runner-provisioned Xcode verification

The reusable `ci_swift.yaml` workflow accepts `runner-verifier: true` to use a
fixed unsigned Xcode verifier installed by the runner operator. Its default
remains direct Xcode build/test execution. Project and scheme discovery are
shared; the verifier lane never creates a simulator during destination discovery.
Leave `destination` empty in this lane, and set `platform` to `ios` or `macos`.

The runner supplies `XCODE_VERIFIER` as an absolute executable path outside the
checkout. Missing provisioning fails verification; it never falls back to direct
Xcode execution. This is an executable contract, not a shell command input.
The workflow passes separate arguments:

```
--project <repository-local Xcode container>
--scheme <scheme>
--platform <ios|macos>
--configuration <Debug|Release>
[--no-tests]
```

The implementation owns prerequisite checks, runtime approval, destination
selection, isolated build work, bounded execution and cleanup of its own devices.
It must reject execution when those checks cannot pass. It returns zero only for
successful verification and nonzero for blocked, failed or interrupted work.
The workflow bounds the verifier step to 18 minutes within a 20-minute job.

Write each test result bundle to a unique directory matching
`.build/xcode-results/**/Results.xcresult` in the checkout. Keep results after
failure or interruption. The workflow uploads available bundles with `always()`
and five-day retention; a missing bundle warns because prerequisite rejection
can happen before tests start. Artifact access follows the consuming repository's
visibility; local workspace cleanup is the runner operator's responsibility.

Executor-specific implementation, provisioning and operational acceptance belong
in the operator's infrastructure. This shared surface contains only the Xcode
contract and fictitious fixtures. Consumer repositories pin the released shared
ref and supply their own project configuration.

This lane provides dependable CI verification. Incremental build reuse and the
local edit/build/test loop are separate concerns; it adds no build cache.
