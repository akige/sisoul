import Foundation
import CryptoKit

/// Vault: mirror libsodium SecretBox semantics on iOS using CryptoKit ChaChaPoly.
/// On-disk layout matches PC daemon (Rust libsodium) so files round-trip.
public final class Vault {
    public let rootDir: URL
    private let symmetricKey: SymmetricKey

    public init(rootDir: URL, key: Data) throws {
        guard key.count == 32 else {
            throw VaultError.keyDerivationFailed
        }
        self.rootDir = rootDir
        self.symmetricKey = SymmetricKey(data: key)
        try FileManager.default.createDirectory(at: rootDir, withIntermediateDirectories: true)
    }

    /// Seal plaintext using ChaCha20-Poly1305 with a fresh random 12-byte nonce.
    /// Produces a layout binary-compatible with the daemon's libsodium SecretBox.
    public func seal(_ plaintext: Data) throws -> Data {
        var nonceBytes = [UInt8](repeating: 0, count: 12)
        let rc = SecRandomCopyBytes(kSecRandomDefault, 12, &nonceBytes)
        guard rc == errSecSuccess else { throw VaultError.ioError("rng failed") }
        let nonce = try ChaChaPoly.Nonce(data: Data(nonceBytes))
        let sealed = try ChaChaPoly.seal(plaintext, using: symmetricKey, nonce: nonce)
        let header = VaultHeader(nonce: Data(nonceBytes))
        var out = header.encode()
        out.append(sealed.ciphertext)
        out.append(sealed.tag)
        return out
    }

    /// Open envelope produced by `seal` (or PC daemon).
    public func open(_ envelope: Data) throws -> Data {
        let (header, body) = try VaultHeader.decode(envelope)
        guard header.algo == 1 else { throw VaultError.malformed("unsupported algo \(header.algo)") }
        guard body.count >= 16 else { throw VaultError.malformed("no tag") }
        let ctLen = body.count - 16
        let ciphertext = body.subdata(in: 0..<ctLen)
        let tag = body.subdata(in: ctLen..<body.count)
        do {
            let nonce = try ChaChaPoly.Nonce(data: header.nonce)
            let sealed = try ChaChaPoly.SealedBox(nonce: nonce, ciphertext: ciphertext, tag: tag)
            return try ChaChaPoly.open(sealed, using: symmetricKey)
        } catch {
            throw VaultError.wrongKey
        }
    }

    /// Write entry atomically: write to .tmp + fsync + rename.
    public func writeEntry(id: String, entry: VaultEntry) throws {
        let plaintext = entry.serialize()
        let envelope = try seal(plaintext)
        let target = rootDir.appendingPathComponent("\(id).sbx")
        let tmp = rootDir.appendingPathComponent("\(id).sbx.tmp")
        try envelope.write(to: tmp, options: .atomic)
        let fm = FileManager.default
        if fm.fileExists(atPath: target.path) {
            _ = try? fm.removeItem(at: target)
        }
        try fm.moveItem(at: tmp, to: target)
    }

    public func readEntry(id: String) throws -> VaultEntry {
        let target = rootDir.appendingPathComponent("\(id).sbx")
        let data = try Data(contentsOf: target)
        let plaintext = try open(data)
        return try VaultEntry.parse(plaintext)
    }

    public func listEntryIDs() throws -> [String] {
        let fm = FileManager.default
        let items = try fm.contentsOfDirectory(at: rootDir, includingPropertiesForKeys: nil)
        return items
            .filter { $0.pathExtension == "sbx" }
            .map { $0.deletingPathExtension().lastPathComponent }
            .sorted()
    }

    public func deleteEntry(id: String) throws {
        let target = rootDir.appendingPathComponent("\(id).sbx")
        try FileManager.default.removeItem(at: target)
    }

    /// Probe envelope: returns true if a valid SISOUL01 magic is present,
    /// regardless of whether this Vault holds the right key.
    public static func probe(_ envelope: Data) -> Bool {
        guard envelope.count >= VaultHeader.headerSize + 16 else { return false }
        let magic = envelope.prefix(8)
        return Array(magic) == VaultHeader.magic
    }

    /// Verify canary frontmatter field; used to detect torn writes / wrong key edge cases
    /// where the AEAD passes but body is from a different vault generation.
    public func verifyCanary(_ entry: VaultEntry, expected: String = "sisoul-canary-v1") -> Bool {
        return entry.frontmatter.canary == expected
    }

    /// Re-encrypt entire vault with a new key (key rotation).
    public func rotateKey(newKey: Data) throws -> Vault {
        guard newKey.count == 32 else { throw VaultError.keyDerivationFailed }
        let newVault = try Vault(rootDir: rootDir, key: newKey)
        let ids = try listEntryIDs()
        for id in ids {
            let entry = try readEntry(id: id)
            try newVault.writeEntry(id: id, entry: entry)
        }
        return newVault
    }

    public var entryCount: Int {
        (try? listEntryIDs().count) ?? 0
    }
}
