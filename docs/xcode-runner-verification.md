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

The implementation owns prerequisite checks, runtime prerequisites, destination
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

## Simulator selection and ownership

For iOS, select the numerically newest installed, available runtime with a device
reported compatible by `xcodebuild -showdestinations` for the requested project,
scheme and configuration. Exclude incompatible destinations and unavailable
runtimes. Prefer an iPhone within that runtime when both phone and tablet match.
Do not pin an OS version or choose a newer incompatible runtime. If none matches,
fail before creating or booting a simulator. The direct lane follows this rule;
the provisioned verifier must implement the same contract.

Create only a new owned device and disable parallel simulator testing. Before
creation, the runner must durably record its unique device name, runtime, device
type and activity identity. Record the returned device ID before boot. An
uncertain creation response must be recoverable by exact recorded identity.
Cleanup runs on success, failure and normal interruption; retain ownership when
deletion fails or when an independent inventory/storage check still finds data.

An `always()` workflow step cannot recover from host failure or a killed runner.
Persistent runners must provision recovery outside the checkout: reconcile the
ownership journal at job start and during storage reclamation, protect active
workers and their child processes, and retry only demonstrably inactive owned
resources. Retain the journal independently of job-history deletion. Never
shutdown all devices, delete unrelated devices or suppress system crash reports.
The direct lane uses `ci-<run>-<attempt>-<job>` device names for operator tracking;
the protected lane leaves device creation and recovery entirely to its verifier.
