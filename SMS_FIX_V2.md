# SMS verification correction — build 6902

The installed first test build switched from SMS to trusted-device validation after an SMS delivery request returned HTTP 403/415. A user then demonstrated that an SMS had actually arrived. Entering that SMS code in the trusted-device prompt produced an incorrect-code error. HTTP status alone did not establish whether a text had been delivered, and switching the validation channel was incorrect for this case.

This revision removes that automatic switch. SMS uses the JSON API implemented by iloader/isideload: PUT `/auth/verify/phone` and POST `/auth/verify/phone/securitycode`, with `mode: sms`, the selected phone number, and a nested security-code field. It refreshes anisette before both requests. JSON validation uses the HTTP result and structured service errors; it does not require the legacy XML endpoint's PE-token header.

After ambiguous delivery 403/415, the app conditionally invites the user to enter a received SMS code or cancel, and always validates that code using the SMS endpoint. It does not send a replacement text, claim delivery definitely failed, or request a trusted-device code. Structured 412 challenges must match the selected phone and unlocked six-digit challenge schema. Recognized replacement-send limits may retain an existing SMS code; authorization, throttling and server failures otherwise stop the attempt.

Genuine trusted-device authentication remains separate, including the existing behavior that does not request another code when Apple already sent one. The prompt title now identifies the actual channel; SMS inputs support iOS one-time-code autofill.

The offline regression harness compiles the actual changed Swift functions against fake HTTP responses. It checks that even ambiguous SMS delivery errors cannot route an SMS code to the trusted-device endpoint, and separately checks that trusted-device codes retain their own endpoint. It also checks JSON body/header agreement, fresh anisette, cancellation, incorrect codes, server failures, active challenges, and request limits.

This is a source correction and test build. Real Apple SMS validation and subsequent app refresh must still be verified on the device after installation. The user's installation-integrity issue was resolved by switching the signing Apple account; this revision does not claim that changing the authentication code repairs certificate/profile rejection.

Mac run [39896930226](https://github.com/Hyoni1129/AltStore-auth-fix/actions/runs/39896930226), source `5d7f2c7`, passed all **69** offline assertions and built the iPhone Release IPA successfully. The downloaded artifact matched its SHA-256. Local packaging verification confirmed build 6902, ARM64, seven patched-code markers, matching widget version, valid ZIP CRCs, zero symlinks, and absence of the obsolete SMS-failed/trusted-device instructions in the authentication binary. Local device preparation changed only the main app Info.plist and preserved all compiled code.

Reference implementation: https://github.com/nab138/isideload/blob/main/isideload/src/auth/apple_account.rs
