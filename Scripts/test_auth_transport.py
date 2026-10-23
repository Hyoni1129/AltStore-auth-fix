#!/usr/bin/env python3
"""Compile production HTTP framing/TLS transport and run offline parser regressions.

The fixtures contain no account data and make no network requests. This verifies
wire framing, not successful authentication against Apple's live service.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'Dependencies/AltSign/AltSign/Sources/AppleAuthenticationTransport.swift'
tests = r'''
import Foundation

var passed = 0
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    precondition(condition(), label); passed += 1
}
func reject(_ label: String, _ action: () throws -> Void) {
    do { try action(); fatalError("Expected rejection: \(label)") }
    catch { passed += 1 }
}
func parse(_ text: String, eof: Bool = false) throws -> (Data, HTTPURLResponse)? {
    try AppleAuthenticationHTTP.parse(Data(text.utf8), eof: eof)
}
let body = Data("<plist>fixture</plist>".utf8)
var request = URLRequest(url: AppleAuthenticationHTTP.endpoint)
request.httpMethod = "POST"
request.httpBody = body
request.setValue("text/x-xml-plist", forHTTPHeaderField: "Content-Type")
request.setValue("fixture-UA", forHTTPHeaderField: "User-Agent")
request.setValue("fixture-client", forHTTPHeaderField: "X-MMe-Client-Info")
request.setValue("gzip", forHTTPHeaderField: "Accept-Encoding")
request.setValue("keep-alive", forHTTPHeaderField: "Connection")
request.setValue("1", forHTTPHeaderField: "Content-Length")
let encoded = try AppleAuthenticationHTTP.encode(request)
let wire = String(decoding: encoded, as: UTF8.self)
check(wire.hasPrefix("POST /grandslam/GsService2 HTTP/1.1\r\n"), "HTTP/1.1 origin-form request")
check(wire.contains("Host: gsa.apple.com\r\n"), "Fixed Apple host")
check(wire.contains("Connection: close\r\n"), "One request per connection")
check(!wire.contains("keep-alive") && !wire.contains("gzip"), "Do not negotiate compression/reuse")
check(wire.contains("Accept-Encoding: identity\r\n"), "Plain response encoding")
check(wire.contains("Content-Length: \(body.count)\r\n"), "Length matches actual bytes")
check(encoded.suffix(body.count) == body, "Body preserved exactly")
check(wire.contains("X-MMe-Client-Info: fixture-client\r\n") && wire.contains("User-Agent: fixture-UA\r\n"), "Anisette identity headers preserved")
for address in ["http://gsa.apple.com/grandslam/GsService2", "https://example.com/grandslam/GsService2", "https://gsa.apple.com/auth/verify/phone", "https://gsa.apple.com:444/grandslam/GsService2", "https://gsa.apple.com/grandslam/GsService2?extra=1", "https://gsa.apple.com/grandslam/GsService2#fragment", "https://user@gsa.apple.com/grandslam/GsService2"] {
    var bad = request; bad.url = URL(string: address)!
    reject("Unexpected destination") { _ = try AppleAuthenticationHTTP.encode(bad) }
}
for method in ["GET", "PUT", "DELETE", "POST\r\nGET"] {
    var bad = request; bad.httpMethod = method
    reject("Unexpected method") { _ = try AppleAuthenticationHTTP.encode(bad) }
}
do {
    var bad = request; bad.httpBody = nil
    reject("Missing body") { _ = try AppleAuthenticationHTTP.encode(bad) }
    bad.httpBody = Data(repeating: 65, count: AppleAuthenticationHTTP.bodyLimit + 1)
    reject("Oversized request") { _ = try AppleAuthenticationHTTP.encode(bad) }
}
check(!AppleAuthenticationHTTP.token("bad:name") && !AppleAuthenticationHTTP.token("") && !AppleAuthenticationHTTP.token("Bad Name"), "Header names reject injection")
check(!AppleAuthenticationHTTP.value("value\r\nInjected: true") && !AppleAuthenticationHTTP.value("bad\u{0}"), "Header values reject injection")

let fixed = "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nX-Fixture: yes\r\n\r\nhello"
for end in 0..<fixed.utf8.count {
    let prefix = Data(fixed.utf8.prefix(end))
    check(try AppleAuthenticationHTTP.parse(prefix) == nil, "Partial fixed-length response waits")
}
let full = try parse(fixed)!
check(full.0 == Data("hello".utf8) && full.1.statusCode == 200, "Fixed response body/status")
check(full.1.value(forHTTPHeaderField: "X-Fixture") == "yes", "Response headers preserved")
check(try parse("HTTP/1.0 200 OK\r\n\r\nhello") == nil, "EOF-framed response waits")
check(try parse("HTTP/1.0 200 OK\r\n\r\nhello", eof: true)!.0 == Data("hello".utf8), "EOF response decoded")
check(try parse("HTTP/1.1 100 Continue\r\n\r\n" + fixed)!.0 == Data("hello".utf8), "Informational response skipped")
for status in [200, 301, 401, 403, 415, 429, 500, 503] {
    let reply = try parse("HTTP/1.1 \(status) Fixture\r\nContent-Length: 0\r\nRetry-After: 60\r\n\r\n")!
    check(reply.1.statusCode == status, "HTTP failures are returned, never replayed or accepted as login")
    check(reply.1.value(forHTTPHeaderField: "Retry-After") == "60", "Retry-After preserved")
}
for code in [204, 304] {
    check(try parse("HTTP/1.1 \(code) Fixture\r\n\r\n")!.0.isEmpty, "No-body status")
}
let chunked = "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2;fixture=yes\r\nhe\r\n3\r\nllo\r\n0\r\nX-Trailer: ignored\r\n\r\n"
for end in 0..<chunked.utf8.count {
    check(try AppleAuthenticationHTTP.parse(Data(chunked.utf8.prefix(end))) == nil, "Partial chunked response waits")
}
check(try parse(chunked)!.0 == Data("hello".utf8), "Chunked response decoded")
for invalid in [
    "HTTP/2 200 OK\r\n\r\n", "HTTP/1.1 nope OK\r\n\r\n", "HTTP/1.1 999 Bad\r\n\r\n",
    "HTTP/1.1 200 OK\r\nBad Name: fixture\r\n\r\n", "HTTP/1.1 200 OK\r\n Folded: fixture\r\n\r\n",
    "HTTP/1.1 200 OK\r\nX-Test: fixture\nInjected: yes\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n", "HTTP/1.1 200 OK\r\nContent-Length: 1,1\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Length: 99999999999999999999\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n\r\n",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
    "HTTP/1.1 101 Switching Protocols\r\n\r\n",
    "HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nxx",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nX\r\n",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n-1\r\n",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n1\r\nx!!",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nContent-Length: 0\r\n\r\n",
    "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\nextra",
    String(repeating: "HTTP/1.1 100 Continue\r\n\r\n", count: 5) + fixed
] {
    reject("Malformed/ambiguous response") { _ = try parse(invalid) }
}
for truncated in ["", "HTTP/1.1 200", String(fixed.dropLast()), String(chunked.dropLast())] {
    reject("Premature EOF") { _ = try parse(truncated, eof: true) }
}
reject("Oversized header") { _ = try parse("HTTP/1.1 200 OK\r\nX: " + String(repeating: "x", count: AppleAuthenticationHTTP.headerLimit)) }
reject("Oversized length") { _ = try parse("HTTP/1.1 200 OK\r\nContent-Length: \(AppleAuthenticationHTTP.bodyLimit + 1)\r\n\r\n") }
reject("Oversized chunk") { _ = try parse("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nffffff\r\n") }
reject("Oversized wire") { _ = try AppleAuthenticationHTTP.parse(Data(repeating: 65, count: AppleAuthenticationHTTP.wireLimit + 1)) }
print("PASS: \(passed) offline isolated HTTP/1.1 transport assertions; no network requests")
'''
# A throwing expression is evaluated before the nonthrowing assertion autoclosure.
tests = tests.replace('func check(_ condition: @autoclosure () -> Bool, _ label: String) {\n    precondition(condition(), label); passed += 1\n}',
                      'func check(_ condition: Bool, _ label: String) {\n    precondition(condition, label); passed += 1\n}')
with tempfile.TemporaryDirectory(prefix='altstore-transport-tests-') as directory:
    main = Path(directory) / 'main.swift'
    main.write_text(tests, encoding='utf-8')
    executable = Path(directory) / 'transport-tests'
    subprocess.run(['swiftc', '-swift-version', '5', str(source), str(main), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
