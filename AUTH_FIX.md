# Authentication test build

Current revision: build 6902. See [SMS_FIX_V2.md](SMS_FIX_V2.md) for the SMS/trusted-device separation correction prompted by the installed-device test. The earlier build's automatic SMS-to-trusted-device fallback has been removed.

Build 6902 completed in [Mac run 39896930226](https://github.com/Hyoni1129/AltStore-auth-fix/actions/runs/39896930226), source `5d7f2c7`, with all **69** offline regression assertions passing. The IPA was downloaded, checksum-verified, and prepared locally. Real SMS login and app Refresh remain to be confirmed on the installed v2.

This is a local experimental patch of AltStore Classic, not an official release or a verified fix.
Upstream: `altstoreio/AltStore`, classic commit `bbc10d6964185b75a46c6ddd7476ac08df095308`.
AltSign base: `e639bdfbea185d5c229b11f62e25c5d78649d5c4`.

## Changes

- Forward the remote anisette provider's User-Agent to Apple authentication requests; reject empty or newline-containing client metadata.
- Connect AltSign's existing fresh-anisette callback to the mobile operation queue. Code validation, the second sign-in, and token retrieval can obtain fresh data without recreating the provisioned identity.
- Keep SMS and trusted-device codes on their respective verification endpoints. SMS uses the JSON API; an ambiguous delivery HTTP 403/415 can prompt for an already-received SMS code without requesting a different kind of code or sending another text.
- Refresh anisette before SMS send and validation. Preserve HTTP failures and structured incorrect-code responses. JSON SMS validation does not require the XML endpoint's PE-token header.
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

The offline harness checks SMS success, channel-specific endpoints, JSON headers/body, fresh anisette before send and validation, 403/415 conditional SMS entry, structured active challenges, incorrect codes, cancellation, refresh failure, 401/429/500/503 propagation, bounded request counts, preservation of already-delivered trusted-device codes, and diagnostic label allowlisting. It uses a fake transport; no network transport exists in the harness. It is not an end-to-end Apple authentication test.

## Device acceptance

1. Preserve the original IPA and back up the existing app's accessible data before replacing it. Do not delete the app or reset pairing as a preparatory step.
2. Run `python3 Scripts/prepare_device_ipa.py unsigned.ipa local-device.ipa --udid YOUR_DEVICE_UDID` on the local signing computer. This restores the app's ALTDeviceID without uploading a device identifier to CI. Sign that copy with the same locally authenticated Apple account used for the existing installation, and verify its final bundle identifier before an update install.
3. Fix the account, selected anisette provider, and network for one login attempt. Enter the password and any verification code directly on the device.
4. Collect only `AltStoreAuth stage=… status=…` entries. If the attempt fails, use the stage and HTTP status to select the next correction. Do not request repeated logins without a code or configuration change that tests a specific hypothesis.
5. Require successful account/team setup, one actual app Refresh, extension of its signing expiry, and successful launch. Check background refresh separately.

## Current validation status

Local patch/whitespace checks, reverse application of the nested AltSign patch, Python syntax, and workflow YAML validation have been checked. A third-party Swift grammar reports no explicit syntax error nodes in the changed files; it also has missing-node reports in the upstream originals, so this does not establish successful Swift compilation.

The user approved publishing a test fork and building the installable IPA. Mac run [39893740393](https://github.com/Hyoni1129/AltStore-auth-fix/actions/runs/39893740393), source commit `eb33d57`, passed all 32 offline Swift regression assertions and completed the Xcode 26.6 iPhone Release build. The command demotes only upstream corecrypto's int8-negation diagnostic from error to warning; it does not change the cryptographic code.

The IPA was downloaded with its matching SHA-256 and prepared for the device locally. Packaging verification confirmed ARM64, six patched-code markers in the application/AltStoreCore framework, ZIP CRC integrity, matching widget version, and zero symlinks. Local preparation changes only the app/widget Info.plist files. The device is disconnected, so installation, real Apple login, and Refresh remain unverified.

## References

- https://github.com/altstoreio/AltStore/pull/1835
- https://github.com/rileytestut/AltSign/pull/59
- https://github.com/rileytestut/AltSign/pull/61
- https://github.com/xtool-org/xtool/pull/349
