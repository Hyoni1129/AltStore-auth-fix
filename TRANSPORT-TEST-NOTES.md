# Authentication transport test build 6903

The reported failure is `init / HTTP 503` immediately after Sign in, before code
entry. It is distinct from the SMS route mismatch addressed by build 6902.
The Apple response alone does not establish throttling, account rejection or a
connection-pool defect.

This experiment removes one transport variable: only POST requests to the fixed
`https://gsa.apple.com/grandslam/GsService2` endpoint now use a new Network.framework
TLS connection with HTTP/1.1 ALPN, `Connection: close`, and no TLS resumption. Each
connection sends exactly one request and is cancelled after a complete response.
There is no retry, fallback connection, redirect following or 503-to-success
conversion. Anisette provider identity headers and the authentication body are
preserved. SMS and trusted-device endpoints retain the 6902 transport and behavior.

TLS requires peer authentication and TLS 1.2 or newer. System certificate/hostname
validation remains enabled, with no custom trust override. HTTP input/output has
bounded headers/body and strict length/chunk framing. Unsupported compressed
responses fail closed. This direct connection does not implement HTTP proxy
configuration or URLSession cookies; any effect of those differences needs device
validation. No cookies were explicitly managed in the previous per-request
ephemeral session implementation.

`Scripts/test_auth_transport.py` compiles the actual production source on macOS
and runs offline request/response framing tests. `Scripts/test_auth_flow.py` retains
the SMS/trusted-device regression tests. Both run before the unsigned iPhone build.
Neither test uses credentials or claims successful live Apple authentication.

If 503 persists on this build, the connection-pool hypothesis remains unsupported;
the next investigation needs a sanitized stage/status trace with the chosen
anisette provider and a controlled comparison to a successful signing client.
Repeated login/SMS requests or deleting pairing data are not part of this patch.
