import Foundation

// Mirror libsodium SecretBox header used by PC daemon.
// On-disk layout:
//   magic(8) = "SISOUL01"
//   version(1) = 0x01
//   algo(1)    = 0x01 (ChaCha20-Poly1305)
//   reserved(2)
//   nonce(12)
//   ciphertext(N)
//   tag(16)
public struct VaultHeader: Equatable {
    public static let magic: [UInt8] = Array("SISOUL01".utf8)
    public static let headerSize = 24 // magic+ver+algo+reserved+nonce
    public let version: UInt8
    public let algo: UInt8
    public let nonce: Data

    public init(version: UInt8 = 1, algo: UInt8 = 1, nonce: Data) {
        self.version = version
        self.algo = algo
        self.nonce = nonce
    }

    public func encode() -> Data {
        var out = Data()
        out.append(contentsOf: VaultHeader.magic)
        out.append(version)
        out.append(algo)
        out.append(contentsOf: [0, 0])
        out.append(nonce)
        return out
    }

    public static func decode(_ data: Data) throws -> (VaultHeader, Data) {
        guard data.count >= headerSize + 16 else {
            throw VaultError.malformed("file too short \(data.count)")
        }
        let magic = data.prefix(8)
        guard Array(magic) == magic.map({ $0 }), Array(magic) == VaultHeader.magic else {
            throw VaultError.malformed("magic mismatch")
        }
        let version = data[8]
        let algo = data[9]
        let nonce = data.subdata(in: 12..<24)
        let body = data.subdata(in: 24..<data.count)
        return (VaultHeader(version: version, algo: algo, nonce: nonce), body)
    }
}

public enum VaultError: Error, Equatable {
    case malformed(String)
    case decryptFailed
    case wrongKey
    case ioError(String)
    case keyDerivationFailed
}

public struct Frontmatter: Codable, Equatable {
    public var id: String
    public var createdAt: String
    public var updatedAt: String
    public var kind: String
    public var tags: [String]
    public var canary: String

    public init(id: String, createdAt: String, updatedAt: String, kind: String, tags: [String] = [], canary: String = "sisoul-canary-v1") {
        self.id = id
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.kind = kind
        self.tags = tags
        self.canary = canary
    }
}

public struct VaultEntry: Codable, Equatable {
    public var frontmatter: Frontmatter
    public var body: String

    public init(frontmatter: Frontmatter, body: String) {
        self.frontmatter = frontmatter
        self.body = body
    }

    public func serialize() -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let fm = (try? encoder.encode(frontmatter)) ?? Data()
        var out = Data()
        out.append("---\n".data(using: .utf8)!)
        out.append(fm)
        out.append("\n---\n".data(using: .utf8)!)
        out.append(body.data(using: .utf8)!)
        return out
    }

    public static func parse(_ data: Data) throws -> VaultEntry {
        guard let text = String(data: data, encoding: .utf8) else {
            throw VaultError.malformed("not utf8")
        }
        let parts = text.components(separatedBy: "\n---\n")
        guard parts.count >= 2 else { throw VaultError.malformed("no frontmatter delimiter") }
        let fmPart = parts[0].replacingOccurrences(of: "---\n", with: "")
        let body = parts.dropFirst().joined(separator: "\n---\n")
        guard let fmData = fmPart.data(using: .utf8) else {
            throw VaultError.malformed("fm not utf8")
        }
        let fm = try JSONDecoder().decode(Frontmatter.self, from: fmData)
        return VaultEntry(frontmatter: fm, body: body)
    }
}

public struct DaemonStatus: Codable, Equatable {
    public let version: String
    public let identityFingerprint: String
    public let vaultEntries: Int
    public let lastSync: String?
}

public struct SyncRequest: Codable {
    public let since: String?
    public let deviceID: String
    public let pushEntries: [String]

    public init(since: String?, deviceID: String, pushEntries: [String]) {
        self.since = since
        self.deviceID = deviceID
        self.pushEntries = pushEntries
    }
}

public struct SyncResponse: Codable {
    public let pulledEntries: [String]
    public let serverTime: String
    public let conflicts: [String]
}
