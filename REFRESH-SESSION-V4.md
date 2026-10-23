# Refresh session test build 6904

The device is confirmed to be running 6903. Its Refresh All error log shows both
apps failing during initial GrandSlam authentication. Separating physical TLS
connections in 6903 therefore did not eliminate the observed 503.

The previous offline tests covered authentication functions and HTTP framing, but
did not cover a new Refresh group after successful sign-in. AppManager creates a
new authenticated context for that group. AuthenticationOperation previously only
retained the password; it started a fresh SRP login for every new group even if a
recently issued developer token remained valid.

## Changes

- Save successful developer tokens in a dedicated, non-synchronizing,
  after-first-unlock-this-device-only keychain service. Do not save anisette OTPs.
- Bind the record to account, anisette device identity and client metadata. Honor
  Apple's millisecond expiry, cap local retention at seven days, and use only a
  15-minute local lifetime when Apple supplies no expiry. Require 60 seconds of
  remaining lifetime before reuse.
- Validate the token with Apple's developer account endpoint using fresh anisette
  data. Only a real successful response permits Refresh to proceed.
- On explicit expired session (developer code 1100) or HTTP 401, invalidate and
  perform one login. Propagate 503, offline and permission errors without another
  password/SMS request. Preserve developer HTTP status on all three API transports.
- Serialize authentication decisions across new Refresh groups. Fetch fresh
  anisette when the queued turn begins. Use separate API instances per operation
  so User-Agent, verification UI and refresh/resync callbacks cannot overwrite
  another operation's state.
- Clear the cache on sign-out, fence in-flight work with a revision, and invalidate
  rejected sessions for the next operation without replaying provisioning changes.
- Protect concurrently collected extension profiles and preserve the first error.
- Give each app operation an independent session copy so refreshing one app's
  anisette data cannot change another app's profile requests.
- Redact the developer-session object description.

## Validation scope

`Scripts/test_refresh_session.py` compiles the real coordinator, app authentication
bridge, token decoder, and on-device profile install function. Its keychain, Apple
service and iPhone client are offline fakes. Cases include login followed by a new
Refresh, new process/coordinator, concurrent groups, stale or corrupt cache,
account/device/client changes, expiry units, explicit invalid session, transient
errors, cancellation, sign-out during requests, duplicate callbacks, and parent/
extension profile installation failure. The existing SMS and HTTP tests remain.

The device profile creation/expiry baseline has been collected locally. Real
acceptance still requires successful Apple authentication, two actual Refreshes,
profile creation/expiry advancement, a fresh app launch and a cold-start Refresh.
Offline results or installation alone must not be reported as end-to-end success.
This change reduces unnecessary password logins; it cannot manufacture a valid
token if the first login is rejected with 503.
