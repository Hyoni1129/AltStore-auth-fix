#!/usr/bin/env python3
"""Compile the actual patched 2FA functions against an offline HTTP transport.

No Apple login, account data, or network requests are used by these tests.
The harness covers control flow; it does not replace an Xcode or real-device test.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "Dependencies/AltSign/AltSign/Sources/ALTAppleAPI+Authentication.swift").read_text(encoding="utf-8")
flow = source[source.index("    func requestTrustedDeviceTwoFactorCode("):source.index("    func fetchAccount(")]
stage = source[source.index("    func authenticationStage(for request:"):source.index("    // GrandSlam rejects requests")]

prelude = r'''
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

let ALTUnderlyingAppleAPIErrorDomain = "OfflineAppleError"
struct ALTAppleAPIError: Error {
    enum Code { case requiresTwoFactorAuthentication, incorrectVerificationCode, unknown }
    let code: Code
    var status: Int? = nil
    init(_ code: Code) { self.code = code }
    static func unknown(statusCode: Int? = nil, failure: String? = nil) -> Self {
        var error = Self(.unknown); error.status = statusCode; return error
    }
}
struct ALTAnisetteData { let generation: Int }
struct Reply {
    let status: Int
    var headers: [String: String] = [:]
    var body: [String: Any] = ["ec": 0]
}
final class ALTAppleAPI {
    var twoFactorAuthenticationHint: String?
    var replies: [Reply] = []
    var requests: [URLRequest] = []
    var refreshCount = 0
    var failRefresh = false
    func refreshAnisetteData(_ old: ALTAnisetteData, completionHandler: @escaping (Result<ALTAnisetteData, Error>) -> Void) {
        refreshCount += 1
        if failRefresh { return completionHandler(.failure(ALTAppleAPIError(.unknown))) }
        completionHandler(.success(ALTAnisetteData(generation: old.generation + 1)))
    }
    func makeTwoFactorCodeRequest(url: URL, dsid: String, idmsToken: String, anisetteData: ALTAnisetteData) -> URLRequest {
        var request = URLRequest(url: url)
        request.setValue(String(anisetteData.generation), forHTTPHeaderField: "X-Test-Generation")
        return request
    }
    func sendGSARequest(_ request: URLRequest, completionHandler: @escaping (Result<(Data, HTTPURLResponse), Error>) -> Void) {
        requests.append(request)
        precondition(!replies.isEmpty, "Unexpected extra HTTP request; no real network transport exists")
        let reply = replies.removeFirst()
        let data = try! PropertyListSerialization.data(fromPropertyList: reply.body, format: .xml, options: 0)
        let response = HTTPURLResponse(url: request.url!, statusCode: reply.status, httpVersion: "HTTP/1.1", headerFields: reply.headers)!
        completionHandler(.success((data, response)))
    }
}
extension ALTAppleAPI {
'''

checks = r'''
}

var passed = 0
func check(_ condition: @autoclosure () -> Bool, _ description: String) {
    precondition(condition(), description)
    passed += 1
}
func sms(_ api: ALTAppleAPI, code: String? = "123456") -> (prompts: Int, completions: Int, result: Result<Void, Error>?) {
    var prompts = 0, completions = 0
    var result: Result<Void, Error>?
    api.requestSMSTwoFactorCode(dsid: "fake-account", idmsToken: "fake-token", anisetteData: ALTAnisetteData(generation: 0), verificationHandler: { handler in
        prompts += 1; handler(code)
    }, completionHandler: { value in completions += 1; result = value })
    return (prompts, completions, result)
}
func status(_ result: Result<Void, Error>?) -> Int? {
    if case .failure(let error) = result { return (error as? ALTAppleAPIError)?.status }
    return nil
}
func succeeded(_ result: Result<Void, Error>?) -> Bool {
    if case .success = result { return true }; return false
}

// Normal SMS delivery still validates with a fresh one-time password.
do {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: 200), Reply(status: 200, headers: ["x-apple-pe-token": "fake"])]
    let result = sms(api)
    check(succeeded(result.result), "SMS success and case-insensitive PE-token header")
    check(result.prompts == 1 && result.completions == 1, "SMS callbacks occur once")
    check(api.requests.count == 2 && api.refreshCount == 1, "SMS validates once with fresh data")
    check(api.requests.last?.value(forHTTPHeaderField: "X-Test-Generation") == "1", "Old anisette is not used after typing a code")
}
// Both observed SMS rejection statuses may use a real trusted-device code.
for rejection in [403, 415] {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: rejection), Reply(status: 200), Reply(status: 200)]
    let result = sms(api)
    check(succeeded(result.result), "SMS rejection falls back to trusted-device validation")
    check(result.prompts == 1 && result.completions == 1, "Fallback callbacks occur once")
    check(api.requests.map { $0.url!.path } == ["/auth/verify/phone/put", "/auth/verify/trusteddevice", "/grandslam/GsService2/validate"], "Fallback uses the trusted-device validation endpoint")
    check(api.refreshCount == 2 && api.requests.last?.value(forHTTPHeaderField: "X-Test-Generation") == "2", "Both fallback stages refresh anisette")
}
// When delivery itself is rejected, a manually supplied code is still checked by Apple.
do {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: 415), Reply(status: 403), Reply(status: 200)]
    let result = sms(api)
    check(succeeded(result.result), "Manual trusted-device code may validate")
    check(result.prompts == 1 && result.completions == 1 && api.twoFactorAuthenticationHint != nil, "Manual-code instructions are shown once")
}
// Never treat an outage or throttling response as success, and never retry it in a loop.
for failure in [401, 429, 500, 503] {
    let api = ALTAppleAPI(); api.replies = [Reply(status: failure)]
    let result = sms(api)
    check(status(result.result) == failure, "SMS request preserves the HTTP failure")
    check(result.prompts == 0 && result.completions == 1 && api.requests.count == 1, "No prompt or retry after SMS transport failure")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 415), Reply(status: 503)]
    let result = sms(api)
    check(status(result.result) == 503 && result.prompts == 0 && api.requests.count == 2, "Fallback outage stops without manual-code bypass")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200), Reply(status: 503)]
    let result = sms(api)
    check(status(result.result) == 503, "Validation 503 is not reported as incorrect verification code")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200)]
    let result = sms(api, code: nil)
    check(!succeeded(result.result) && result.completions == 1 && api.requests.count == 1, "Cancel sends no validation request")
}
do {
    let api = ALTAppleAPI(); api.failRefresh = true; api.replies = [Reply(status: 200)]
    let result = sms(api)
    check(!succeeded(result.result) && result.completions == 1 && api.requests.count == 1, "Refresh failure does not reuse stale data")
}
do {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: 415), Reply(status: 200), Reply(status: 200, body: ["ec": -21669])]
    let result = sms(api)
    check(!succeeded(result.result) && result.completions == 1, "Fallback does not accept an incorrect code")
}
// Apple's already-delivered code must not be invalidated by requesting a replacement.
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200)]
    var completions = 0
    api.requestTrustedDeviceTwoFactorCode(dsid: "fake", idmsToken: "fake", anisetteData: ALTAnisetteData(generation: 0), isCodeAlreadySent: true, verificationHandler: { $0("123456") }, completionHandler: { _ in completions += 1 })
    check(completions == 1 && api.requests.count == 1 && api.requests[0].url!.path == "/grandslam/GsService2/validate", "Already-sent code is only validated, not re-requested")
}
// Diagnostics are restricted to an allowlist; arbitrary body values cannot become log labels.
do {
    let api = ALTAppleAPI()
    var request = URLRequest(url: URL(string: "https://gsa.apple.com/grandslam/GsService2")!)
    for operation in ["init", "complete", "apptokens"] {
        request.httpBody = try! PropertyListSerialization.data(fromPropertyList: ["Request": ["o": operation, "token": "never-log-this"]], format: .xml, options: 0)
        check(api.authenticationStage(for: request) == operation, "Known operation is classified")
    }
    request.httpBody = try! PropertyListSerialization.data(fromPropertyList: ["Request": ["o": "never-log-this"]], format: .xml, options: 0)
    check(api.authenticationStage(for: request) == "sign-in", "Unknown operation cannot leak into diagnostics")
}
print("PASS: \(passed) offline authentication regression assertions; no network requests")
'''

with tempfile.TemporaryDirectory(prefix="altstore-auth-tests-") as directory:
    path = Path(directory) / "AuthFlowTests.swift"
    path.write_text(prelude + flow + stage + checks, encoding="utf-8")
    subprocess.run(["swift", str(path)], check=True)
