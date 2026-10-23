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
    var json: Bool = false
}
final class ALTAppleAPI {
    var twoFactorAuthenticationHint: String?
    var twoFactorAuthenticationTitle: String?
    var replies: [Reply] = []
    var requests: [URLRequest] = []
    var refreshCount = 0
    var failRefresh = false
    var failRefreshAt: Int?
    func refreshAnisetteData(_ old: ALTAnisetteData, completionHandler: @escaping (Result<ALTAnisetteData, Error>) -> Void) {
        refreshCount += 1
        if failRefresh || failRefreshAt == refreshCount { return completionHandler(.failure(ALTAppleAPIError(.unknown))) }
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
        let data: Data
        if reply.json { data = try! JSONSerialization.data(withJSONObject: reply.body) }
        else { data = try! PropertyListSerialization.data(fromPropertyList: reply.body, format: .xml, options: 0) }
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
    precondition(condition(), description); passed += 1
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
    if case .failure(let error) = result { return (error as? ALTAppleAPIError)?.status }; return nil
}
func succeeded(_ result: Result<Void, Error>?) -> Bool {
    if case .success = result { return true }; return false
}
func incorrect(_ result: Result<Void, Error>?) -> Bool {
    if case .failure(let error) = result, let error = error as? ALTAppleAPIError,
       case .incorrectVerificationCode = error.code { return true }; return false
}
func jsonBody(_ request: URLRequest) -> [String: Any] {
    return try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
}

// The code received by SMS must always go to the SMS JSON validation endpoint.
for delivery in [200, 403, 415] {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: delivery), Reply(status: 204)]
    let result = sms(api)
    check(succeeded(result.result), "SMS code validates even when delivery returned an ambiguous error")
    check(result.prompts == 1 && result.completions == 1, "One prompt and completion")
    check(api.requests.map { $0.url!.path } == ["/auth/verify/phone", "/auth/verify/phone/securitycode"], "No trusted-device switch or replacement SMS")
    check(api.requests.map { $0.httpMethod! } == ["PUT", "POST"], "SMS methods match the JSON API")
    check(api.requests.allSatisfy { $0.value(forHTTPHeaderField: "Content-Type") == "application/json" && $0.value(forHTTPHeaderField: "Accept") == "application/json" }, "SMS headers match the body")
    check(api.refreshCount == 2 && api.requests.last?.value(forHTTPHeaderField: "X-Test-Generation") == "2", "Both SMS requests use fresh anisette")
    let body = jsonBody(api.requests.last!)
    check((body["securityCode"] as? [String: String])?["code"] == "123456" && body["mode"] as? String == "sms", "Code remains in the SMS schema")
    check((body["phoneNumber"] as? [String: Int])?["id"] == 1, "Selected phone number is preserved")
    check(api.twoFactorAuthenticationTitle?.contains("SMS") == true, "Prompt identifies the actual code type")
    check(api.twoFactorAuthenticationHint?.contains("trusted") == false && api.twoFactorAuthenticationHint?.contains("delivery failed") == false, "No false failed-delivery/trusted-device instructions")
}

// A 412 is an active SMS challenge only when its complete structured data matches.
let challenge: [String: Any] = [
    "mode": "sms", "type": "verification", "authenticationType": "hsa2",
    "trustedPhoneNumber": ["id": 1], "trustedPhoneNumbers": [["id": 1]],
    "securityCode": ["length": 6, "tooManyCodesSent": false, "tooManyCodesValidated": false,
                     "securityCodeLocked": false, "securityCodeCooldown": false]
]
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 412, body: challenge, json: true), Reply(status: 200)]
    let result = sms(api)
    check(succeeded(result.result) && result.prompts == 1 && api.requests.count == 2, "Active SMS challenge validates without a second send")
}
for field in ["tooManyCodesSent", "tooManyCodesValidated", "securityCodeLocked", "securityCodeCooldown", "length"] {
    var invalid = challenge
    var flags = invalid["securityCode"] as! [String: Any]
    if field == "length" { flags[field] = 8 }
    else { flags[field] = true }
    invalid["securityCode"] = flags
    let api = ALTAppleAPI(); api.replies = [Reply(status: 412, body: invalid, json: true)]
    let result = sms(api)
    check(!succeeded(result.result) && result.prompts == 0 && api.requests.count == 1, "Locked, limited or malformed challenge is rejected")
}
do {
    var invalid = challenge; invalid["trustedPhoneNumber"] = ["id": 2]
    let api = ALTAppleAPI(); api.replies = [Reply(status: 412, body: invalid, json: true)]
    let result = sms(api)
    check(!succeeded(result.result) && result.prompts == 0, "A challenge for a different phone is rejected")
}
for serviceCode in ["-22979", "-22981"] {
    let api = ALTAppleAPI()
    api.replies = [Reply(status: 403, body: ["serviceErrors": [["code": serviceCode]]], json: true), Reply(status: 200)]
    let result = sms(api)
    check(succeeded(result.result) && api.requests.count == 2, "A last SMS code may validate when sending a replacement is limited")
}
for failure in [400, 401, 429, 500, 503] {
    let api = ALTAppleAPI(); api.replies = [Reply(status: failure)]
    let result = sms(api)
    check(status(result.result) == failure, "Request HTTP failure preserved")
    check(result.prompts == 0 && result.completions == 1 && api.requests.count == 1, "No extra request on transport failure")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 403, body: ["serviceErrors": [["code": "-28248"]]], json: true)]
    let result = sms(api)
    check(!succeeded(result.result) && result.prompts == 0, "A terminal service error is not an active SMS challenge")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200), Reply(status: 400, body: ["serviceErrors": [["code": "-21669"]]], json: true)]
    let result = sms(api)
    check(incorrect(result.result) && result.completions == 1, "Only Apple's incorrect-code response is reported as incorrect code")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200), Reply(status: 503)]
    let result = sms(api)
    check(status(result.result) == 503 && !incorrect(result.result), "Validation outage is not an incorrect code")
}
do {
    let api = ALTAppleAPI(); api.replies = [Reply(status: 200), Reply(status: 200, body: ["serviceErrors": [["code": "-21669"]]], json: true)]
    check(!succeeded(sms(api).result), "Semantic errors cannot pass because HTTP was 200")
}
for delivery in [200, 415] {
    let api = ALTAppleAPI(); api.replies = [Reply(status: delivery)]
    let result = sms(api, code: nil)
    check(!succeeded(result.result) && result.completions == 1 && api.requests.count == 1, "Cancel sends no validation or trusted-device request")
}
for failAt in [1, 2] {
    let api = ALTAppleAPI(); api.failRefreshAt = failAt; api.replies = [Reply(status: 200)]
    let result = sms(api)
    check(!succeeded(result.result) && api.requests.count == failAt - 1, "Fresh-data failure never reuses stale headers")
}
// Genuine trusted-device codes retain their distinct path and never become SMS codes.
for alreadySent in [false, true] {
    let api = ALTAppleAPI()
    api.replies = alreadySent ? [Reply(status: 200)] : [Reply(status: 200), Reply(status: 200)]
    var outcome: Result<Void, Error>?
    api.requestTrustedDeviceTwoFactorCode(dsid: "fake", idmsToken: "fake", anisetteData: ALTAnisetteData(generation: 0), isCodeAlreadySent: alreadySent, verificationHandler: { $0("123456") }, completionHandler: { outcome = $0 })
    check(succeeded(outcome), "Trusted-device authentication still validates")
    check(api.requests.last!.url!.path == "/grandslam/GsService2/validate", "Trusted-device code uses its own endpoint")
    check(api.requests.count == (alreadySent ? 1 : 2), "Already-delivered code is not requested again")
    check(api.twoFactorAuthenticationTitle?.contains("trusted") == true && api.twoFactorAuthenticationHint == nil, "Trusted-device prompt has no stale SMS error")
}
// Log labels are restricted to operations, never arbitrary response/request values.
do {
    let api = ALTAppleAPI()
    var request = URLRequest(url: URL(string: "https://gsa.apple.com/grandslam/GsService2")!)
    for operation in ["init", "complete", "apptokens", "never-log-this"] {
        request.httpBody = try! PropertyListSerialization.data(fromPropertyList: ["Request": ["o": operation]], format: .xml, options: 0)
        check(api.authenticationStage(for: request) == (operation == "never-log-this" ? "sign-in" : operation), "Diagnostic operation allowlist")
    }
}
print("PASS: \(passed) offline authentication regression assertions; no network requests")

'''

with tempfile.TemporaryDirectory(prefix="altstore-auth-tests-") as directory:
    path = Path(directory) / "AuthFlowTests.swift"
    path.write_text(prelude + flow + stage + checks, encoding="utf-8")
    subprocess.run(["swift", str(path)], check=True)
