//
//  AuthenticationOperation.swift
//  AltStore
//
//  Created by Riley Testut on 6/5/19.
//  Copyright © 2019 Riley Testut. All rights reserved.
//

import Foundation
import Roxas
import Network

import AltStoreCore
import AltSign

private extension UIColor
{
    static let altInvertedPrimary = UIColor(named: "SettingsHighlighted")!
}

typealias AuthenticationError = AuthenticationErrorCode.Error
enum AuthenticationErrorCode: Int, ALTErrorEnum, CaseIterable
{
    case noTeam
    case noCertificate
    
    case missingPrivateKey
    case missingCertificate
    
    var errorFailureReason: String {
        switch self {
        case .noTeam: return NSLocalizedString("Your Apple ID has no developer teams.", comment: "")
        case .noCertificate: return NSLocalizedString("The developer certificate could not be found.", comment: "")
        case .missingPrivateKey: return NSLocalizedString("The certificate's private key could not be found.", comment: "")
        case .missingCertificate: return NSLocalizedString("The certificate could not be found.", comment: "")
        }
    }
}

@objc(AuthenticationOperation)
class AuthenticationOperation: ResultOperation<(ALTTeam, ALTCertificate, ALTAppleAPISession)>, @unchecked Sendable
{
    let context: AuthenticatedOperationContext
    
    private var presentingViewController: UIViewController? {
        return self.context.presentingViewController
    }
    
    private lazy var navigationController: UINavigationController = {
        let navigationController = self.storyboard.instantiateViewController(withIdentifier: "navigationController") as! UINavigationController
        navigationController.isModalInPresentation = true
        return navigationController
    }()
    
    private lazy var storyboard = UIStoryboard(name: "Authentication", bundle: nil)
    
    private var appleIDEmailAddress: String?
    private var appleIDPassword: String?
    private var shouldShowInstructions = false
    
    private let operationQueue = OperationQueue()
    private let appleAPI = ALTAppleAPI()
    private static let sessionCoordinator = AuthenticationSessionCoordinator<(ALTAccount, ALTAppleAPISession)>()
    private var authenticationRevision: UInt64?
    
    private var submitCodeAction: UIAlertAction?
    
    init(context: AuthenticatedOperationContext, presentingViewController: UIViewController?)
    {
        self.context = context
        
        if let presentingViewController
        {
            // Only set if non-nil to avoid accidentally resetting existing value.
            self.context.presentingViewController = presentingViewController
        }
        
        super.init()
        
        self.context.authenticationOperation = self
        self.operationQueue.name = "com.altstore.AuthenticationOperation"
        self.progress.totalUnitCount = 4
    }
    
    override func main()
    {
        super.main()
        
        if let error = self.context.error
        {
            self.finish(.failure(error))
            return
        }
                
        // Sign In
        self.signIn() { (result) in
            guard !self.isCancelled else { return self.finish(.failure(OperationError.cancelled)) }
            
            switch result
            {
            case .failure(let error): self.finish(.failure(error))
            case .success((let account, let session)):
                self.context.session = session
                self.progress.completedUnitCount += 1
                
                // Fetch Team
                self.fetchTeam(for: account, session: session) { (result) in
                    guard !self.isCancelled else { return self.finish(.failure(OperationError.cancelled)) }
                    
                    switch result
                    {
                    case .failure(let error): self.finish(.failure(error))
                    case .success(let team):
                        self.context.team = team
                        self.progress.completedUnitCount += 1
                        
                        // Fetch Certificate
                        self.fetchCertificate(for: team, session: session) { (result) in
                            guard !self.isCancelled else { return self.finish(.failure(OperationError.cancelled)) }
                            
                            switch result
                            {
                            case .failure(let error): self.finish(.failure(error))
                            case .success(let certificate):
                                self.context.certificate = certificate
                                self.progress.completedUnitCount += 1
                                       
                                // Register Device
                                self.registerCurrentDevice(for: team, session: session) { (result) in
                                    guard !self.isCancelled else { return self.finish(.failure(OperationError.cancelled)) }
                                    
                                    switch result
                                    {
                                    case .failure(let error): self.finish(.failure(error))
                                    case .success(let device):
                                        UserDefaults.shared.deviceID = device.identifier
                                        self.progress.completedUnitCount += 1
                                        
                                        // Save account/team to disk.
                                        self.save(team) { (result) in
                                            guard !self.isCancelled else { return self.finish(.failure(OperationError.cancelled)) }
                                            
                                            switch result
                                            {
                                            case .failure(let error): self.finish(.failure(error))
                                            case .success:
                                                // Must cache App IDs _after_ saving account/team to disk.
                                                self.cacheAppIDs(team: team, session: session) { (result) in
                                                    let result = result.map { _ in (team, certificate, session) }
                                                    self.finish(result)
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    
    func save(_ altTeam: ALTTeam, completionHandler: @escaping (Result<Void, Error>) -> Void)
    {
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        context.performAndWait {
            do
            {
                let account: Account
                let team: Team
                
                if let tempAccount = Account.first(satisfying: NSPredicate(format: "%K == %@", #keyPath(Account.identifier), altTeam.account.identifier), in: context)
                {
                    account = tempAccount
                }
                else
                {
                    account = Account(altTeam.account, context: context)
                }
                
                if let tempTeam = Team.first(satisfying: NSPredicate(format: "%K == %@", #keyPath(Team.identifier), altTeam.identifier), in: context)
                {
                    team = tempTeam
                }
                else
                {
                    team = Team(altTeam, account: account, context: context)
                }
                
                account.update(account: altTeam.account)
                
                if let providedEmailAddress = self.appleIDEmailAddress
                {
                    // Save the user's provided email address instead of the one associated with their account (which may be outdated).
                    account.appleID = providedEmailAddress
                }
                
                team.update(team: altTeam)
                                
                try context.save()
                
                completionHandler(.success(()))
            }
            catch
            {
                completionHandler(.failure(error))
            }
        }
    }
    
    override func finish(_ result: Result<(ALTTeam, ALTCertificate, ALTAppleAPISession), Error>)
    {
        guard !self.isFinished else { return }
        if let revision = self.authenticationRevision, revision != Keychain.shared.authenticationSessionRevision()
        {
            super.finish(.failure(OperationError.cancelled))
            return
        }
        
        switch result
        {
        case .failure(let error): Logger.sideload.error("Failed to authenticate account. \(error.localizedDescription, privacy: .public)")
        case .success((let team, _, _)): Logger.sideload.notice("Authenticated account for team \(team.identifier, privacy: .public).")
        }
        
        let context = DatabaseManager.shared.persistentContainer.newBackgroundContext()
        context.perform {
            do
            {
                let (altTeam, altCertificate, session) = try result.get()
                
                guard
                    let account = Account.first(satisfying: NSPredicate(format: "%K == %@", #keyPath(Account.identifier), altTeam.account.identifier), in: context),
                    let team = Team.first(satisfying: NSPredicate(format: "%K == %@", #keyPath(Team.identifier), altTeam.identifier), in: context)
                else { throw AuthenticationError(.noTeam) }
                
                // Account
                account.isActiveAccount = true
                
                let otherAccountsFetchRequest = Account.fetchRequest() as NSFetchRequest<Account>
                otherAccountsFetchRequest.predicate = NSPredicate(format: "%K != %@", #keyPath(Account.identifier), account.identifier)
                
                let otherAccounts = try context.fetch(otherAccountsFetchRequest)
                for account in otherAccounts
                {
                    account.isActiveAccount = false
                }
                
                // Team
                team.isActiveTeam = true
                
                let otherTeamsFetchRequest = Team.fetchRequest() as NSFetchRequest<Team>
                otherTeamsFetchRequest.predicate = NSPredicate(format: "%K != %@", #keyPath(Team.identifier), team.identifier)
                
                let otherTeams = try context.fetch(otherTeamsFetchRequest)
                for team in otherTeams
                {
                    team.isActiveTeam = false
                }
                
                let activeAppsMinimumVersion = OperatingSystemVersion(majorVersion: 13, minorVersion: 3, patchVersion: 1)
                if team.type == .free, ProcessInfo.processInfo.isOperatingSystemAtLeast(activeAppsMinimumVersion)
                {
                    UserDefaults.standard.activeAppsLimit = InstalledApp.freeAccountActiveAppsLimit
                }
                else
                {
                    UserDefaults.standard.activeAppsLimit = nil
                }
                
                // Save
                try context.save()
                
                // Update keychain
                Keychain.shared.appleIDEmailAddress = self.appleIDEmailAddress ?? altTeam.account.appleID // Prefer the user's provided email address over the one associated with their account (which may be outdated).
                Keychain.shared.appleIDPassword = self.appleIDPassword
                
                Keychain.shared.signingCertificate = altCertificate.p12Data()
                Keychain.shared.signingCertificatePassword = altCertificate.machineIdentifier
                
                self.showInstructionsIfNecessary() { (didShowInstructions) in
                    
                    let signer = ALTSigner(team: altTeam, certificate: altCertificate)
                    // Refresh screen must go last since a successful refresh will cause the app to quit.
                    self.showRefreshScreenIfNecessary(signer: signer, session: session) { (didShowRefreshAlert) in
                        super.finish(result)
                        
                        DispatchQueue.main.async {
                            self.navigationController.dismiss(animated: true, completion: nil)
                        }
                    }
                }
            }
            catch
            {
                super.finish(result)
                
                DispatchQueue.main.async {
                    self.navigationController.dismiss(animated: true, completion: nil)
                }
            }
        }
    }
}

private extension AuthenticationOperation
{
    func present(_ viewController: UIViewController) -> Bool
    {
        guard let presentingViewController = self.presentingViewController else { return false }
        
        self.navigationController.view.tintColor = .altInvertedPrimary
        
        if self.navigationController.viewControllers.isEmpty
        {
            guard presentingViewController.presentedViewController == nil else { return false }
            
            self.navigationController.setViewControllers([viewController], animated: false)            
            presentingViewController.present(self.navigationController, animated: true, completion: nil)
        }
        else
        {
            viewController.navigationItem.leftBarButtonItem = nil
            self.navigationController.pushViewController(viewController, animated: true)
        }
        
        return true
    }
}

private extension AuthenticationOperation
{
    func signIn(completionHandler: @escaping (Result<(ALTAccount, ALTAppleAPISession), Swift.Error>) -> Void)
    {
        func authenticate()
        {
            DispatchQueue.main.async {
                let authenticationViewController = self.storyboard.instantiateViewController(withIdentifier: "authenticationViewController") as! AuthenticationViewController
                authenticationViewController.authenticationHandler = { (appleID, password, completionHandler) in
                    self.authenticate(appleID: appleID, password: password) { (result) in
                        completionHandler(result)
                    }
                }
                authenticationViewController.completionHandler = { (result) in
                    if let (account, session, password) = result
                    {
                        // We presented the Auth UI and the user signed in.
                        // In this case, we'll assume we should show the instructions again.
                        self.shouldShowInstructions = true
                        
                        self.appleIDPassword = password
                        completionHandler(.success((account, session)))
                    }
                    else
                    {
                        completionHandler(.failure(OperationError.cancelled))
                    }
                }
                
                if !self.present(authenticationViewController)
                {
                    completionHandler(.failure(OperationError.notAuthenticated))
                }
            }
        }
        
        if let appleID = Keychain.shared.appleIDEmailAddress, let password = Keychain.shared.appleIDPassword
        {
            Logger.sideload.notice("Authenticating Apple ID...")
            
            self.authenticate(appleID: appleID, password: password, allowsCachedSession: true) { (result) in
                switch result
                {
                case .success((let account, let session)):
                    self.appleIDPassword = password
                    completionHandler(.success((account, session)))
                    
                case .failure(ALTAppleAPIError.incorrectCredentials), .failure(ALTAppleAPIError.appSpecificPasswordRequired):
                    authenticate()
                    
                case .failure(let error):
                    completionHandler(.failure(error))
                }
            }
        }
        else
        {
            authenticate()
        }
    }
    
    func authenticate(appleID: String, password: String, allowsCachedSession: Bool = false, completionHandler: @escaping (Result<(ALTAccount, ALTAppleAPISession), Swift.Error>) -> Void)
    {
        self.appleIDEmailAddress = appleID
        let revision = Keychain.shared.authenticationSessionRevision()
        self.authenticationRevision = revision

        self.appleAPI.authenticationUserAgent = nil
        self.appleAPI.twoFactorAuthenticationHint = nil
        self.appleAPI.twoFactorAuthenticationTitle = nil
        self.appleAPI.anisetteDataRefreshHandler = { [weak self] completionHandler in
            guard let self else { return completionHandler(nil, OperationError.unknown()) }

            let refreshOperation = FetchAnisetteDataOperation(context: self.context)
            refreshOperation.authenticationAPI = self.appleAPI
            refreshOperation.resultHandler = { result in
                switch result
                {
                case .failure(let error): completionHandler(nil, error)
                case .success(let anisetteData): completionHandler(anisetteData, nil)
                }
            }
            self.operationQueue.addOperation(refreshOperation)
        }
        
        // Apple can ask for the anisette data to be resynchronized partway through signing in (hsc 434),
        // which only the anisette server that provisioned it can do.
        self.appleAPI.anisetteDataResyncHandler = { [weak self] (sim, completionHandler) in
            guard let self else { return completionHandler(nil, OperationError.unknown()) }

            let resyncOperation = FetchAnisetteDataOperation(context: self.context)
            resyncOperation.authenticationAPI = self.appleAPI
            resyncOperation.resynchronizationData = sim
            resyncOperation.resultHandler = { (result) in
                switch result
                {
                case .failure(let error): completionHandler(nil, error)
                case .success(let anisetteData): completionHandler(anisetteData, nil)
                }
            }

            self.operationQueue.addOperation(resyncOperation)
        }

        let fetchAnisetteDataOperation = FetchAnisetteDataOperation(context: self.context)
        fetchAnisetteDataOperation.authenticationAPI = self.appleAPI
        fetchAnisetteDataOperation.resultHandler = { (result) in
            switch result
            {
            case .failure(let error): completionHandler(.failure(error))
            case .success(let anisetteData):
                let verificationHandler: ((@escaping (String?) -> Void) -> Void)?
                
                if let presentingViewController = self.presentingViewController
                {
                    verificationHandler = { (completionHandler) in
                        DispatchQueue.main.async {
                            let title = self.appleAPI.twoFactorAuthenticationTitle ?? NSLocalizedString("Please enter the 6-digit verification code that was sent to your Apple devices.", comment: "")
                            let alertController = UIAlertController(title: title, message: self.appleAPI.twoFactorAuthenticationHint, preferredStyle: .alert)
                            alertController.addTextField { (textField) in
                                textField.autocorrectionType = .no
                                textField.autocapitalizationType = .none
                                textField.keyboardType = .numberPad
                                textField.textContentType = .oneTimeCode
                                
                                NotificationCenter.default.addObserver(self, selector: #selector(AuthenticationOperation.textFieldTextDidChange(_:)), name: UITextField.textDidChangeNotification, object: textField)
                            }
                            
                            let submitAction = UIAlertAction(title: NSLocalizedString("Continue", comment: ""), style: .default) { (action) in
                                let textField = alertController.textFields?.first
                                
                                let code = textField?.text ?? ""
                                completionHandler(code)
                            }
                            submitAction.isEnabled = false
                            alertController.addAction(submitAction)
                            self.submitCodeAction = submitAction
                            
                            alertController.addAction(UIAlertAction(title: RSTSystemLocalizedString("Cancel"), style: .cancel) { (action) in
                                completionHandler(nil)
                            })
                            
                            if self.navigationController.presentingViewController != nil
                            {
                                self.navigationController.present(alertController, animated: true, completion: nil)
                            }
                            else
                            {
                                presentingViewController.present(alertController, animated: true, completion: nil)
                            }
                        }
                    }
                }
                else
                {
                    // No view controller to present security code alert, so don't provide verificationHandler.
                    verificationHandler = nil
                }
                
                self.authenticateUsingSession(appleID: appleID, password: password,
                    anisetteData: anisetteData, allowsCachedSession: allowsCachedSession,
                    revision: revision,
                    verificationHandler: verificationHandler, completionHandler: completionHandler)
            }
        }
        
        self.operationQueue.addOperation(fetchAnisetteDataOperation)
    }
    
    func authenticateUsingSession(appleID: String, password: String, anisetteData: ALTAnisetteData,
                                  allowsCachedSession: Bool,
                                  revision: UInt64,
                                  verificationHandler: ((@escaping (String?) -> Void) -> Void)?,
                                  completionHandler: @escaping (Result<(ALTAccount, ALTAppleAPISession), Error>) -> Void)
    {
        let scope = AuthenticationSessionScope(account: appleID, deviceID: anisetteData.deviceUniqueIdentifier,
                                               clientInfo: anisetteData.deviceDescription)
        // A different Refresh group may have waited for interactive 2FA. Fetch again when its
        // serialized turn starts, so queued operations cannot send an old one-time password.
        func freshAnisette(_ completion: @escaping (Result<ALTAnisetteData, Error>) -> Void)
        {
            guard let refresh = self.appleAPI.anisetteDataRefreshHandler else { return completion(.failure(OperationError.unknown())) }
            refresh { data, error in
                guard let data else { return completion(.failure(error ?? OperationError.unknown())) }
                guard data.deviceUniqueIdentifier == scope.deviceID, data.deviceDescription == scope.clientInfo else
                {
                    return completion(.failure(ALTAppleAPIError(.invalidAnisetteData)))
                }
                completion(.success(data))
            }
        }
        Self.sessionCoordinator.resolve(scope: scope, allowsCachedSession: allowsCachedSession,
            load: { Keychain.shared.cachedAuthenticationSession() },
            persist: { Keychain.shared.storeAuthenticationSession($0, revision: revision) },
            isCurrent: {
                Keychain.shared.authenticationSessionRevision() == revision && !self.isCancelled &&
                (!allowsCachedSession || Keychain.shared.appleIDEmailAddress?.lowercased() == appleID.lowercased())
            },
            isInvalidSession: ALTAppleAPI.isAuthenticationSessionInvalid,
            validate: { saved, completion in
                freshAnisette { result in
                    do {
                        let data = try result.get()
                        let session = ALTAppleAPISession(dsid: saved.dsid, authToken: saved.token, anisetteData: data)
                        session.expirationDate = saved.expiresAt
                        self.appleAPI.validateAuthenticationSession(session) { result in
                            if case .success = result { NSLog("AltStoreAuth session=validated-cache") }
                            completion(result.map { ($0, session) })
                        }
                    } catch { completion(.failure(error)) }
                }
            },
            login: { completion in
                freshAnisette { result in
                    do {
                        let data = try result.get()
                        NSLog("AltStoreAuth session=fresh-login")
                        self.appleAPI.authenticate(appleID: appleID, password: password, anisetteData: data,
                                                  verificationHandler: verificationHandler) { account, session, error in
                            if let account, let session { completion(.success((account, session))) }
                            else { completion(.failure(error ?? OperationError.unknown())) }
                        }
                    } catch { completion(.failure(error)) }
                }
            },
            credential: { value in
                AuthenticationSessionCredential(scope: scope, dsid: value.1.dsid, token: value.1.authToken,
                                                issuedAt: Date(), serverExpiry: value.1.expirationDate)
            }, completion: completionHandler)
    }

    func fetchTeam(for account: ALTAccount, session: ALTAppleAPISession, completionHandler: @escaping (Result<ALTTeam, Swift.Error>) -> Void)
    {
        func selectTeam(from teams: [ALTTeam])
        {
            if let team = teams.first(where: { $0.type == .individual })
            {
                return completionHandler(.success(team))
            }
            else if let team = teams.first(where: { $0.type == .free })
            {
                return completionHandler(.success(team))
            }
            else if let team = teams.first
            {
                return completionHandler(.success(team))
            }
            else
            {
                return completionHandler(.failure(AuthenticationError(.noTeam)))
            }
        }

        self.appleAPI.fetchTeams(for: account, session: session) { (teams, error) in
            switch Result(teams, error)
            {
            case .failure(let error): completionHandler(.failure(error))
            case .success(let teams):
                DatabaseManager.shared.persistentContainer.performBackgroundTask { (context) in
                    if let activeTeam = DatabaseManager.shared.activeTeam(in: context), let altTeam = teams.first(where: { $0.identifier == activeTeam.identifier })
                    {
                        completionHandler(.success(altTeam))
                    }
                    else
                    {
                        selectTeam(from: teams)
                    }
                }
            }
        }
    }
    
    func fetchCertificate(for team: ALTTeam, session: ALTAppleAPISession, completionHandler: @escaping (Result<ALTCertificate, Swift.Error>) -> Void)
    {
        func requestCertificate()
        {
            let machineName = "AltStore - " + UIDevice.current.name
            self.appleAPI.addCertificate(machineName: machineName, to: team, session: session) { (certificate, error) in
                do
                {
                    let certificate = try Result(certificate, error).get()
                    guard let privateKey = certificate.privateKey else { throw AuthenticationError(.missingPrivateKey) }
                    
                    self.appleAPI.fetchCertificates(for: team, session: session) { (certificates, error) in
                        do
                        {
                            let certificates = try Result(certificates, error).get()
                            
                            guard let certificate = certificates.first(where: { $0.serialNumber == certificate.serialNumber }) else {
                                throw AuthenticationError(.missingCertificate)
                            }
                            
                            certificate.privateKey = privateKey
                            completionHandler(.success(certificate))
                        }
                        catch
                        {
                            completionHandler(.failure(error))
                        }
                    }
                }
                catch
                {
                    completionHandler(.failure(error))
                }
            }
        }
        
        func replaceCertificate(from certificates: [ALTCertificate])
        {
            Task<Void, Never> { @MainActor in
                do
                {
                    guard
                        let certificate = certificates.first(where: { $0.machineName?.starts(with: "AltStore") == true }) ?? certificates.first
                    else { throw AuthenticationError(.noCertificate) }
                    
                    do
                    {
                        let certificate = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<ALTCertificate, Error>) in
                            let importCertificateViewController = self.storyboard.instantiateViewController(withIdentifier: "importCertificateViewController") as! ImportCertificateViewController
                            importCertificateViewController.validCertificates = certificates
                            importCertificateViewController.completionHandler = { result in
                                continuation.resume(with: result)
                            }
                            
                            if !self.present(importCertificateViewController)
                            {
                                continuation.resume(throwing: AuthenticationError(.noCertificate))
                            }
                        }
                        
                        completionHandler(.success(certificate))
                    }
                    catch is CancellationError
                    {
                        // Cancel == revoke existing certificate
                        try await self.appleAPI.revoke(certificate, for: team, session: session)
                        requestCertificate()
                    }
                }
                catch
                {
                    completionHandler(.failure(error))
                }
            }
        }
        
        self.appleAPI.fetchCertificates(for: team, session: session) { (certificates, error) in
            do
            {
                let certificates = try Result(certificates, error).get()
                
                if
                    let data = Keychain.shared.signingCertificate,
                    let localCertificate = ALTCertificate(p12Data: data, password: nil),
                    let certificate = certificates.first(where: { $0.serialNumber == localCertificate.serialNumber })
                {
                    // We have a certificate stored in the keychain and it hasn't been revoked.
                    localCertificate.machineIdentifier = certificate.machineIdentifier
                    completionHandler(.success(localCertificate))
                }
                else if
                    let serialNumber = Keychain.shared.signingCertificateSerialNumber,
                    let privateKey = Keychain.shared.signingCertificatePrivateKey,
                    let certificate = certificates.first(where: { $0.serialNumber == serialNumber })
                {
                    // LEGACY
                    // We have the private key for one of the certificates, so add it to certificate and use it.
                    certificate.privateKey = privateKey
                    completionHandler(.success(certificate))
                }
                else if
                    let serialNumber = Bundle.main.object(forInfoDictionaryKey: Bundle.Info.certificateID) as? String,
                    let certificate = certificates.first(where: { $0.serialNumber == serialNumber }),
                    let machineIdentifier = certificate.machineIdentifier,
                    FileManager.default.fileExists(atPath: Bundle.main.certificateURL.path),
                    let data = try? Data(contentsOf: Bundle.main.certificateURL),
                    let localCertificate = ALTCertificate(p12Data: data, password: machineIdentifier)
                {
                    // We have an embedded certificate that hasn't been revoked.
                    localCertificate.machineIdentifier = machineIdentifier
                    completionHandler(.success(localCertificate))
                }
                else if certificates.isEmpty
                {
                    // No certificates, so request a new one.
                    requestCertificate()
                }
                else
                {
                    // We don't have private keys for any of the certificates,
                    // so we need to revoke one and create a new one.
                    replaceCertificate(from: certificates)
                }
            }
            catch
            {
                completionHandler(.failure(error))
            }
        }
    }
    
    func registerCurrentDevice(for team: ALTTeam, session: ALTAppleAPISession, completionHandler: @escaping (Result<ALTDevice, Error>) -> Void)
    {
        Task<Void, Never> { @MainActor in
            do
            {
                let devices = try await self.appleAPI.fetchDevices(for: team, types: [.iphone, .ipad], session: session)
                if let udid = UserDefaults.shared.deviceID, let device = devices.first(where: { $0.identifier == udid })
                {
                    completionHandler(.success(device))
                    return
                }
                
                while true
                {
                    guard let presentingViewController else { throw OperationError.unknownUDID }
                    
                    do
                    {
                        let udid: String
                        if let deviceID = UserDefaults.shared.deviceID
                        {
                            udid = deviceID
                        }
                        else
                        {
                            udid = try await AppManager.shared.requestDeviceUDID(presentingViewController: presentingViewController)
                        }
                        
                        do
                        {
                            if let device = devices.first(where: { $0.identifier == udid })
                            {
                                completionHandler(.success(device))
                            }
                            else
                            {
                                let device = try await self.appleAPI.registerDevice(name: UIDevice.current.name, identifier: udid, type: .iphone, team: team, session: session)
                                completionHandler(.success(device))
                            }
                            
                            return
                        }
                        catch
                        {
                            let retryAction = UIAlertAction(title: NSLocalizedString("Try Again", comment: ""), style: .default)
                            let skipAction = UIAlertAction(title: NSLocalizedString("Cancel", comment: ""), style: .cancel)
                            
                            let presentingViewController = self.navigationController.viewControllers.isEmpty ? presentingViewController : self.navigationController
                            try await presentingViewController.presentConfirmationAlert(title: NSLocalizedString("Unable to Register UDID", comment: ""), message: error.localizedDescription, primaryAction: retryAction, cancelAction: skipAction)
                        }
                    }
                }
            }
            catch
            {
                completionHandler(.failure(error))
            }
        }
    }
    
    func cacheAppIDs(team: ALTTeam, session: ALTAppleAPISession, completionHandler: @escaping (Result<Void, Error>) -> Void)
    {
        let fetchAppIDsOperation = FetchAppIDsOperation(context: self.context)
        fetchAppIDsOperation.resultHandler = { (result) in
            do
            {
                let (_, context) = try result.get()
                try context.save()
                
                completionHandler(.success(()))
            }
            catch
            {
                completionHandler(.failure(error))
            }
        }
        
        self.operationQueue.addOperation(fetchAppIDsOperation)
    }
    
    func showInstructionsIfNecessary(completionHandler: @escaping (Bool) -> Void)
    {
        guard self.shouldShowInstructions else { return completionHandler(false) }
        
        DispatchQueue.main.async {
            let instructionsViewController = self.storyboard.instantiateViewController(withIdentifier: "instructionsViewController") as! InstructionsViewController
            instructionsViewController.showsBottomButton = true
            instructionsViewController.completionHandler = {
                completionHandler(true)
            }
            
            if !self.present(instructionsViewController)
            {
                completionHandler(false)
            }
        }
    }
    
    func showRefreshScreenIfNecessary(signer: ALTSigner, session: ALTAppleAPISession, completionHandler: @escaping (Bool) -> Void)
    {
        #if NOTARIZED
        
        // Notarized builds never need to ask user to refresh AltStore.
        completionHandler(false)
        
        #else
        
        guard let application = ALTApplication(fileURL: Bundle.main.bundleURL), let provisioningProfile = application.provisioningProfile else { return completionHandler(false) }
        
        // If we're not using the same certificate used to install AltStore, warn user that they need to refresh.
        guard !provisioningProfile.certificates.contains(signer.certificate) else { return completionHandler(false) }
        
        #if DEBUG
        completionHandler(false)
        #else
        DispatchQueue.main.async {
            let context = AuthenticatedOperationContext(context: self.context)
            context.operations.removeAllObjects() // Prevent deadlock due to endless waiting on previous operations to finish.
            
            let refreshViewController = self.storyboard.instantiateViewController(withIdentifier: "refreshAltStoreViewController") as! RefreshAltStoreViewController
            refreshViewController.context = context
            refreshViewController.completionHandler = { _ in
                completionHandler(true)
            }
            
            if !self.present(refreshViewController)
            {
                completionHandler(false)
            }
        }
        #endif // DEBUG
        
        #endif // NOTARIZED
    }
}

extension AuthenticationOperation
{
    @objc func textFieldTextDidChange(_ notification: Notification)
    {
        guard let textField = notification.object as? UITextField else { return }
        
        self.submitCodeAction?.isEnabled = (textField.text ?? "").count == 6
    }
}
