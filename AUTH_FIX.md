# Authentication test build

This is a local experimental patch of AltStore Classic, not an official release or a verified fix.
Upstream: `altstoreio/AltStore`, classic commit `bbc10d6964185b75a46c6ddd7476ac08df095308`.
AltSign base: `e639bdfbea185d5c229b11f62e25c5d78649d5c4`.

## Changes

- Forward the remote anisette provider's User-Agent to Apple authentication requests; reject empty or newline-containing client metadata.
- Connect AltSign's existing fresh-anisette callback to the mobile operation queue. Code validation, the second sign-in, and token retrieval can obtain fresh data without recreating the provisioned identity.
- On SMS delivery HTTP 403/415 only, try the trusted-device path once. If that delivery request also returns 403/415, allow a current trusted-device code to be supplied manually. A successful Apple code validation is still required. HTTP 429/503 and network errors do not trigger a retry loop or a manual-code bypass.
- Refresh anisette before SMS code validation. Preserve HTTP failures during validation instead of presenting every failure as an incorrect code. Read the PE-token header without depending on its letter case.
- Log only allowlisted authentication stages and numeric HTTP status. Do not print response bodies, headers, account identifiers, passwords, codes, or tokens.

These address concrete source-level integration gaps and the observed SMS failure path. They do not guarantee that Apple will accept every client/account/server combination or that -80046 is fixed.

## Build

On a Mac with a compatible Xcode selected:

```sh
git submodule update --init --recursive
bash Scripts/build_auth_ipa.sh
```

The build applies `Patches/AltSign-auth-fix.patch` to the pinned AltSign submodule and first compiles/runs an offline regression harness extracted from the actual patched two-factor functions. It then builds an unsigned iOS app and packages `build/AltStore-auth-test.ipa` with a checksum. The unsigned build is named **AltStore Auth Test**, build 6901, and keeps the upstream app identifier so a local signing tool can update the existing app where the signing identity matches.

The CI workflow uses a standard GitHub-hosted macOS runner with read-only repository permissions. It does not require an Apple account, certificate, provisioning profile, pairing file, or device identifier. Account authentication and signing must happen locally after downloading the artifact.

The offline harness checks SMS success, case-insensitive PE-token headers, fresh anisette after code entry, 403/415 fallback, manual-code validation, incorrect codes, cancellation, refresh failure, 401/429/500/503 propagation, bounded request counts, preservation of already-delivered codes, and diagnostic label allowlisting. It uses a fake transport; no network transport exists in the harness. It is not an end-to-end Apple authentication test.

## Device acceptance

1. Preserve the original IPA and back up the existing app's accessible data before replacing it. Do not delete the app or reset pairing as a preparatory step.
2. Run `python3 Scripts/prepare_device_ipa.py unsigned.ipa local-device.ipa --udid YOUR_DEVICE_UDID` on the local signing computer. This restores the app's ALTDeviceID without uploading a device identifier to CI. Sign that copy with the same locally authenticated Apple account used for the existing installation, and verify its final bundle identifier before an update install.
3. Fix the account, selected anisette provider, and network for one login attempt. Enter the password and any verification code directly on the device.
4. Collect only `AltStoreAuth stage=… status=…` entries. If the attempt fails, use the stage and HTTP status to select the next correction. Do not request repeated logins without a code or configuration change that tests a specific hypothesis.
5. Require successful account/team setup, one actual app Refresh, extension of its signing expiry, and successful launch. Check background refresh separately.

## Current validation status

Local patch/whitespace checks, reverse application of the nested AltSign patch, Python syntax, and workflow YAML validation have been checked. A third-party Swift grammar reports no explicit syntax error nodes in the changed files; it also has missing-node reports in the upstream originals, so this does not establish successful Swift compilation.

The user approved publishing a test fork and building the installable IPA. The first Mac run passed all 32 offline Swift regression assertions. Xcode 26.6 then stopped on upstream corecrypto's int8 negation warning promoted to an error. The build command demotes only that diagnostic from error to warning; it does not change the cryptographic code. App compilation and device login/Refresh still need verification.

## References

- https://github.com/altstoreio/AltStore/pull/1835
- https://github.com/rileytestut/AltSign/pull/59
- https://github.com/rileytestut/AltSign/pull/61
- https://github.com/xtool-org/xtool/pull/349
