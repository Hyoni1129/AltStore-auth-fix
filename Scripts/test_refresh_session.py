#!/usr/bin/env python3
"""Exercise the production session coordinator, app integration and profile installer offline.

The keychain, Apple service and iPhone client are fakes. This is not live Refresh
acceptance: a device-issued profile with an advanced expiry is still required.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
api = (ROOT / 'Dependencies/AltSign/AltSign/Sources/ALTAppleAPI+Authentication.swift').read_text()
operation = (ROOT / 'AltStore/Operations/AuthenticationOperation.swift').read_text()
refresh = (ROOT / 'AltStore/Operations/RefreshAppOperation.swift').read_text()
helper = operation[operation.index('    func authenticateUsingSession('):operation.index('    func fetchTeam(')]
invalid = api[api.index('    static func isAuthenticationSessionInvalid('):api.index('    // Revalidate a stored')]
tokens = api[api.index('    func fetchAuthToken('):api.index('    func requestTrustedDeviceTwoFactorCode(')]
installer = refresh[refresh.index('    func refreshOnDevice('):refresh.index('    func refreshViaServer(')]

# Check the real call sites in addition to executing the bridge below.
assert 'allowsCachedSession: true' in operation[operation.index('if let appleID = Keychain.shared.appleIDEmailAddress'):operation.index('    func authenticate(appleID:')]
assert 'allowsCachedSession' not in operation[operation.index('authenticationViewController.authenticationHandler'):operation.index('authenticationViewController.completionHandler')]
assert operation.count('authenticationAPI = self.appleAPI') == 3
assert 'ALTAppleAPI.shared' not in operation
assert 'authenticationRevision &+= 1' in (ROOT / 'AltStoreCore/Components/Keychain.swift').read_text()
assert 'ALTAppleAPISession(session: $0)' in (ROOT / 'AltStore/Operations/OperationContexts.swift').read_text()
assert 'case .success(let anisetteData): context.session?.anisetteData = anisetteData' in (ROOT / 'AltStore/Managing Apps/AppManager.swift').read_text()

prelude = r'''
import Foundation
import OSLog
let ALTUnderlyingAppleAPIErrorDomain = "FixtureUnderlyingApple"
let ALTAppleAPIErrorDomain = "FixtureApple"
let ALTHTTPStatusCode = "FixtureHTTPStatus"
struct ALTAppleAPIError: Error {
    enum Code { case authenticationHandshakeFailed, invalidAnisetteData }
    init(_ code: Code) {}
}
struct OperationError: Error { static func unknown() -> Self { Self() } }
struct GSAContext {}
extension Data { func decryptedGCM(context: GSAContext) -> Data? { self } }
struct ALTAnisetteData {
    var deviceUniqueIdentifier = "fixture-device"
    var deviceDescription = "fixture-client"
    var generation = 1
}
final class ALTAccount {}
final class ALTAppleAPISession {
    let dsid: String
    let authToken: String
    let anisetteData: ALTAnisetteData
    var expirationDate: Date?
    init(dsid: String, authToken: String, anisetteData: ALTAnisetteData) {
        self.dsid = dsid; self.authToken = authToken; self.anisetteData = anisetteData
        expirationDate = Date().addingTimeInterval(86400)
    }
}
final class Keychain {
    static let shared = Keychain()
    var appleIDEmailAddress: String? = "fixture@example.invalid"
    private let lock = NSLock()
    private var data: Data?
    private var revision: UInt64 = 0
    func authenticationSessionRevision() -> UInt64 { lock.lock(); defer { lock.unlock() }; return revision }
    func cachedAuthenticationSession() -> Data? { lock.lock(); defer { lock.unlock() }; return data }
    func storeAuthenticationSession(_ value: Data?, revision: UInt64) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard self.revision == revision else { return false }; data = value; return true
    }
    func reset() { lock.lock(); defer { lock.unlock() }; revision += 1; data = nil }
}
final class ALTAppleAPI {
    var freshData: ALTAnisetteData? = ALTAnisetteData(generation: 9)
    var freshError: Error?
    var refreshCalls = 0
    lazy var anisetteDataRefreshHandler: ((@escaping (ALTAnisetteData?, Error?) -> Void) -> Void)? = { [weak self] completion in
        guard let self else { return }; self.refreshCalls += 1; completion(self.freshData, self.freshError)
    }
    var loginCalls = 0
    var validationCalls = 0
    var validatedGeneration: Int?
    var validationError: Error?
    var loginError: Error?
    var beforeLogin: (() -> Void)?
    var beforeValidation: (() -> Void)?
    var duplicateCallback = false
    var tokenFixture: [String: Any] = [:]
    func sendAuthenticationRequest(parameters: [String: Any], anisetteData: ALTAnisetteData,
                                   completionHandler: (Result<[String: Any], Error>) -> Void) {
        completionHandler(.success(["et": try! PropertyListSerialization.data(fromPropertyList: tokenFixture, format: .xml, options: 0)]))
    }
    func validateAuthenticationSession(_ session: ALTAppleAPISession, completionHandler: (Result<ALTAccount, Error>) -> Void) {
        validationCalls += 1; validatedGeneration = session.anisetteData.generation
        beforeValidation?()
        let result: Result<ALTAccount, Error> = validationError.map { .failure($0) } ?? .success(ALTAccount())
        completionHandler(result)
        if duplicateCallback { completionHandler(result) }
    }
    func authenticate(appleID: String, password: String, anisetteData: ALTAnisetteData,
                      verificationHandler: ((@escaping (String?) -> Void) -> Void)?,
                      completionHandler: (ALTAccount?, ALTAppleAPISession?, Error?) -> Void) {
        loginCalls += 1; beforeLogin?()
        if let error = loginError { completionHandler(nil, nil, error); return }
        let session = ALTAppleAPISession(dsid: "fixture-dsid", authToken: "fixture-token-secret", anisetteData: anisetteData)
        completionHandler(ALTAccount(), session, nil)
        if duplicateCallback { completionHandler(ALTAccount(), session, nil) }
    }
'''
middle = r'''
}
final class AuthenticationOperation {
    let appleAPI = ALTAppleAPI()
    static var sessionCoordinator = AuthenticationSessionCoordinator<(ALTAccount, ALTAppleAPISession)>()
    var isCancelled = false
'''
profile_prelude = r'''
}
extension Logger { static let sideload = Logger(subsystem: "offline.fixture", category: "refresh") }
extension NSError { func withLocalizedFailure(_ message: String) -> NSError { self } }
struct ALTProvisioningProfile: Hashable { let bundleIdentifier: String }
final class FakeDevice {
    var installed: [ALTProvisioningProfile] = []
    var calls = 0
    var fail = false
    func installProvisioningProfile(_ profile: ALTProvisioningProfile) async throws {
        calls += 1
        if fail { throw NSError(domain: "FixtureDevice", code: 2002) }
        installed.append(profile)
    }
}
final class AppManager {
    static let shared = AppManager()
    var device = FakeDevice()
    func makeOnDeviceClient() throws -> FakeDevice { device }
}
final class RefreshAppOperation {
'''
checks = r'''
}
var passed = 0
func check(_ value: Bool, _ label: String) { precondition(value, label); passed += 1 }
func success<T>(_ value: Result<T, Error>?) -> Bool { if case .success? = value { return true }; return false }
func error(_ code: Int, domain: String = ALTUnderlyingAppleAPIErrorDomain, status: Int? = nil) -> NSError {
    NSError(domain: domain, code: code, userInfo: status.map { [ALTHTTPStatusCode: $0] } ?? [:])
}
let account = "fixture@example.invalid"
let scope = AuthenticationSessionScope(account: account, deviceID: "fixture-device", clientInfo: "fixture-client")
func record(expiry: Date? = Date().addingTimeInterval(86400), issued: Date = Date(), scoped: AuthenticationSessionScope = scope) -> AuthenticationSessionCredential {
    AuthenticationSessionCredential(scope: scoped, dsid: "fixture-dsid", token: "fixture-token-secret", issuedAt: issued, serverExpiry: expiry)
}
func seed(_ saved: AuthenticationSessionCredential = record()) {
    let keychain = Keychain.shared
    _ = keychain.storeAuthenticationSession(try! JSONEncoder().encode(saved), revision: keychain.authenticationSessionRevision())
}
func resolve(_ operation: AuthenticationOperation = AuthenticationOperation(), cached: Bool = true,
             email: String = account, anisette: ALTAnisetteData = ALTAnisetteData(generation: 2), revision: UInt64? = nil) -> Result<(ALTAccount, ALTAppleAPISession), Error>? {
    let done = DispatchSemaphore(value: 0)
    var outcome: Result<(ALTAccount, ALTAppleAPISession), Error>?
    operation.authenticateUsingSession(appleID: email, password: "fixture-password", anisetteData: anisette,
        allowsCachedSession: cached, revision: revision ?? Keychain.shared.authenticationSessionRevision(),
        verificationHandler: nil) { outcome = $0; done.signal() }
    precondition(done.wait(timeout: .now() + 5) == .success, "Authentication deadlocked")
    return outcome
}

// Initial login -> new Refresh group -> restarted app: only the first step logs in.
Keychain.shared.reset()
do {
    let initial = AuthenticationOperation()
    check(success(resolve(initial, cached: false)), "Initial login succeeds")
    check(initial.appleAPI.loginCalls == 1 && initial.appleAPI.validationCalls == 0, "Interactive login is never bypassed")
    check(Keychain.shared.cachedAuthenticationSession() != nil, "Successful token retained")
    let refreshing = AuthenticationOperation(); refreshing.appleAPI.loginError = error(3000, domain: ALTAppleAPIErrorDomain, status: 503)
    check(success(resolve(refreshing)), "Refresh succeeds despite password-login endpoint being unavailable")
    check(refreshing.appleAPI.loginCalls == 0 && refreshing.appleAPI.validationCalls == 1, "Refresh validates the token without starting SRP init")
    check(refreshing.appleAPI.validatedGeneration == 9, "Cached token fetches fresh anisette after its queued turn starts")
    AuthenticationOperation.sessionCoordinator = AuthenticationSessionCoordinator()
    let restarted = AuthenticationOperation()
    check(success(resolve(restarted)) && restarted.appleAPI.loginCalls == 0, "Cache survives a new coordinator/app lifetime")
}

// Two independent app Refresh groups, submitted together, share the completed login.
Keychain.shared.reset()
do {
    let operations = [AuthenticationOperation(), AuthenticationOperation()]
    let done = DispatchSemaphore(value: 0)
    for operation in operations {
        operation.authenticateUsingSession(appleID: account, password: "fixture-password", anisetteData: ALTAnisetteData(),
            allowsCachedSession: true, revision: Keychain.shared.authenticationSessionRevision(), verificationHandler: nil) { result in
                precondition(success(result)); done.signal()
            }
    }
    for _ in operations { check(done.wait(timeout: .now() + 5) == .success, "Concurrent Refresh completes") }
    check(operations.map { $0.appleAPI.loginCalls }.reduce(0, +) == 1, "Concurrent groups perform one password login")
    check(operations.map { $0.appleAPI.validationCalls }.reduce(0, +) == 1, "Queued group validates newly stored token")
}

for invalidError in [error(1100), error(3000, domain: ALTAppleAPIErrorDomain, status: 401)] {
    Keychain.shared.reset(); seed()
    let operation = AuthenticationOperation(); operation.appleAPI.validationError = invalidError
    check(success(resolve(operation)), "Expired/unauthorized token recovers")
    check(operation.appleAPI.validationCalls == 1 && operation.appleAPI.loginCalls == 1, "Explicit invalid session triggers exactly one login")
}
for transient in [error(3000, domain: ALTAppleAPIErrorDomain, status: 503), error(3000, domain: ALTAppleAPIErrorDomain, status: 403), URLError(.notConnectedToInternet) as NSError, error(1100, domain: "DifferentDomain")] {
    Keychain.shared.reset(); seed(); let before = Keychain.shared.cachedAuthenticationSession()
    let operation = AuthenticationOperation(); operation.appleAPI.validationError = transient
    check(!success(resolve(operation)), "Service/offline/permission error propagates")
    check(operation.appleAPI.loginCalls == 0, "Transient failure does not amplify password/SMS requests")
    check(Keychain.shared.cachedAuthenticationSession() == before, "Transient failure preserves token")
}

let staleRecords = [record(expiry: Date().addingTimeInterval(-1)), record(expiry: Date().addingTimeInterval(30)),
                    record(issued: Date().addingTimeInterval(600)),
                    record(scoped: AuthenticationSessionScope(account: "other@example.invalid", deviceID: "fixture-device", clientInfo: "fixture-client")),
                    record(scoped: AuthenticationSessionScope(account: account, deviceID: "different-device", clientInfo: "fixture-client")),
                    record(scoped: AuthenticationSessionScope(account: account, deviceID: "fixture-device", clientInfo: "different-client"))]
for saved in staleRecords {
    Keychain.shared.reset(); seed(saved)
    let operation = AuthenticationOperation()
    check(success(resolve(operation)), "Ineligible token gets a new login")
    check(operation.appleAPI.validationCalls == 0 && operation.appleAPI.loginCalls == 1, "Wrong account/device/client or expired token is never reused")
}
for corrupt in [Data("not-json".utf8), Data(repeating: 65, count: 65537)] {
    Keychain.shared.reset(); _ = Keychain.shared.storeAuthenticationSession(corrupt, revision: Keychain.shared.authenticationSessionRevision())
    let operation = AuthenticationOperation()
    check(success(resolve(operation)) && operation.appleAPI.loginCalls == 1, "Corrupt or oversized cache is discarded")
}
Keychain.shared.reset(); seed()
do {
    let operation = AuthenticationOperation()
    check(success(resolve(operation, cached: false)) && operation.appleAPI.loginCalls == 1 && operation.appleAPI.validationCalls == 0, "Manual credential entry always validates supplied password")
}
for duringValidation in [false, true] {
    Keychain.shared.reset(); if duringValidation { seed() }
    let operation = AuthenticationOperation()
    if duringValidation { operation.appleAPI.beforeValidation = { Keychain.shared.reset() } }
    else { operation.appleAPI.beforeLogin = { Keychain.shared.reset() } }
    check(!success(resolve(operation)), "Sign out invalidates an in-flight authentication result")
    check(Keychain.shared.cachedAuthenticationSession() == nil, "Sign out cannot be undone by late token persistence")
}
Keychain.shared.reset()
do {
    let oldRevision = Keychain.shared.authenticationSessionRevision(); Keychain.shared.reset()
    let operation = AuthenticationOperation()
    check(!success(resolve(operation, revision: oldRevision)) && operation.appleAPI.loginCalls == 0, "Queued operation from old account cannot start login")
    operation.isCancelled = true
    check(!success(resolve(operation)) && operation.appleAPI.loginCalls == 0, "Cancelled operation cannot start login")
}
for cached in [false, true] {
    Keychain.shared.reset(); if cached { seed() }
    let operation = AuthenticationOperation(); operation.appleAPI.duplicateCallback = true
    check(success(resolve(operation, cached: cached)), "Duplicate callback cannot repeat completion/login")
    check(operation.appleAPI.loginCalls == (cached ? 0 : 1), "No extra login on duplicate callback")
}
Keychain.shared.reset(); seed()
do {
    let operation = AuthenticationOperation(); operation.appleAPI.validationError = error(1100); operation.appleAPI.loginError = error(3000, domain: ALTAppleAPIErrorDomain, status: 503)
    check(!success(resolve(operation)) && operation.appleAPI.loginCalls == 1, "503 after expired token stops without retry loop")
    check(Keychain.shared.cachedAuthenticationSession() == nil, "Rejected old token is removed even if new login fails")
}
let now = Date()
Keychain.shared.appleIDEmailAddress = nil
do {
    let operation = AuthenticationOperation()
    check(!success(resolve(operation)) && operation.appleAPI.loginCalls == 0, "A removed saved account cannot be silently reactivated")
}
Keychain.shared.appleIDEmailAddress = account
for missing in [false, true] {
    Keychain.shared.reset(); seed()
    let operation = AuthenticationOperation()
    if missing { operation.appleAPI.freshData = nil; operation.appleAPI.freshError = URLError(.notConnectedToInternet) }
    else { operation.appleAPI.freshData = ALTAnisetteData(deviceUniqueIdentifier: "changed-device", deviceDescription: "fixture-client", generation: 10) }
    check(!success(resolve(operation)), "Missing or changed anisette data stops cache validation")
    check(operation.appleAPI.validationCalls == 0 && operation.appleAPI.loginCalls == 0, "Never send stale or mismatched queued anisette data")
}
check(record(expiry: nil, issued: now).expiresAt == now.addingTimeInterval(900), "Missing expiry has a short local lifetime")
check(record(expiry: now.addingTimeInterval(30 * 86400), issued: now).expiresAt == now.addingTimeInterval(7 * 86400), "Local lifetime is capped")
check(!record().description.contains("secret") && !record().debugDescription.contains("secret"), "Record descriptions redact tokens")

// Execute the actual app-token decoder and confirm Apple's milliseconds are not read as seconds.
do {
    let api = ALTAppleAPI()
    api.tokenFixture = ["t": ["fixture-app": ["token": "fixture-token", "expiry": 2000000000000 as Int64]]]
    var token: (value: String, expiry: Date?)?
    api.fetchAuthToken(app: "fixture-app", parameters: [:], context: GSAContext(), anisetteData: ALTAnisetteData()) { token = try? $0.get() }
    check(token?.expiry?.timeIntervalSince1970 == 2000000000, "Server expiry parsed in milliseconds")
    for bad in ["invalid", -1] as [Any] {
        api.tokenFixture = ["t": ["fixture-app": ["token": "fixture-token", "expiry": bad]]]
        var failed = false
        api.fetchAuthToken(app: "fixture-app", parameters: [:], context: GSAContext(), anisetteData: ALTAnisetteData()) { if case .failure = $0 { failed = true } }
        check(failed, "Malformed server expiry rejected")
    }
}

// Execute the actual on-device profile install function with a fake iPhone client.
for fails in [false, true] {
    AppManager.shared.device = FakeDevice(); AppManager.shared.device.fail = fails
    let done = DispatchSemaphore(value: 0)
    var succeeded = false
    Task.detached {
        do {
            try await RefreshAppOperation().refreshOnDevice(profiles: Set([
                ALTProvisioningProfile(bundleIdentifier: "fixture.app"), ALTProvisioningProfile(bundleIdentifier: "fixture.app.widget")]))
            succeeded = true
        } catch { }
        done.signal()
    }
    check(done.wait(timeout: .now() + 5) == .success, "Device profile operation completes")
    check(succeeded == !fails, "Profile installation errors propagate")
    check(AppManager.shared.device.installed.count == (fails ? 0 : 2), "Parent and extension profiles install only on real client success")
    check(AppManager.shared.device.calls == (fails ? 1 : 2), "Stop after first profile installation failure")
}
print("PASS: \(passed) offline Refresh/session integration assertions; fake keychain, Apple and iPhone")
'''

with tempfile.TemporaryDirectory(prefix='altstore-refresh-tests-') as directory:
    main = Path(directory) / 'main.swift'
    main.write_text(prelude + invalid + tokens + middle + helper + profile_prelude + installer + checks)
    executable = Path(directory) / 'refresh-tests'
    production = ROOT / 'Dependencies/AltSign/AltSign/Sources/AuthenticationSessionReuse.swift'
    subprocess.run(['swiftc', '-swift-version', '5', str(production), str(main), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
    # Compile the actual Objective-C session implementation to check per-app isolation.
    directory = Path(directory)
    (directory / 'ALTAccount.h').write_text('#import <Foundation/Foundation.h>\n')
    (directory / 'ALTAnisetteData.h').write_text('''#import <Foundation/Foundation.h>
@interface ALTAnisetteData : NSObject <NSCopying>
@property (nonatomic, copy) NSString *oneTimePassword;
@end
''')
    (directory / 'main.m').write_text(r'''
#import <Foundation/Foundation.h>
#import "ALTAppleAPISession.h"
#import "ALTAnisetteData.h"
@implementation ALTAnisetteData
- (id)copyWithZone:(NSZone *)zone {
    ALTAnisetteData *copy = [ALTAnisetteData new]; copy.oneTimePassword = self.oneTimePassword; return copy;
}
@end
int main(void) {
    @autoreleasepool {
        ALTAnisetteData *initial = [ALTAnisetteData new]; initial.oneTimePassword = @"fixture-old";
        ALTAppleAPISession *parent = [[ALTAppleAPISession alloc] initWithDSID:@"fixture-dsid" authToken:@"fixture-token-secret" anisetteData:initial];
        parent.expirationDate = [NSDate dateWithTimeIntervalSince1970:2000000000];
        ALTAppleAPISession *first = [[ALTAppleAPISession alloc] initWithSession:parent];
        ALTAppleAPISession *second = [[ALTAppleAPISession alloc] initWithSession:parent];
        NSCAssert(first != parent && second != first, @"Independent session objects");
        NSCAssert(first.anisetteData != parent.anisetteData, @"Independent anisette objects");
        NSCAssert([first.authToken isEqual:parent.authToken], @"Token preserved");
        NSCAssert([first.expirationDate isEqual:parent.expirationDate], @"Expiry preserved");
        first.anisetteData.oneTimePassword = @"fixture-new";
        NSCAssert([parent.anisetteData.oneTimePassword isEqual:@"fixture-old"], @"First app cannot mutate shared context");
        NSCAssert([second.anisetteData.oneTimePassword isEqual:@"fixture-old"], @"First app cannot mutate second app");
        NSCAssert(![parent.description containsString:@"fixture-token-secret"], @"Description redacts token");
        NSCAssert(![parent.description containsString:@"fixture-old"], @"Description redacts anisette");
        puts("PASS: 8 offline Objective-C session isolation assertions; no credentials or network");
    }
}
''')
    api_directory = ROOT / 'Dependencies/AltSign/AltSign/Apple API'
    executable = directory / 'session-tests'
    subprocess.run(['clang', '-fobjc-arc', '-fmodules', '-framework', 'Foundation',
                    '-I', str(directory), '-I', str(api_directory), str(api_directory / 'ALTAppleAPISession.m'),
                    str(directory / 'main.m'), '-o', str(executable)], check=True)
    subprocess.run([str(executable)], check=True)
