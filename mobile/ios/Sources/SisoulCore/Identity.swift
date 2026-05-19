import Foundation
import CryptoKit

// BIP-39 helper (subset: 12-word mnemonic 验证 + PBKDF2 master key 派生).
// 跟 PC 端 src/sisoul/identity/seed.py mnemonic_to_master_key 对齐.
//
// 注: 完整 BIP-39 wordlist (2048 词) 需嵌入 bundle, 这里只暴露接口 + 校验.

public struct Mnemonic {
    public let words: [String]

    public init(words: [String]) throws {
        guard words.count == 12 || words.count == 24 else {
            throw IdentityError.invalidMnemonicLength(words.count)
        }
        self.words = words
    }

    public init(joined: String) throws {
        let tokens = joined.split(whereSeparator: { $0.isWhitespace }).map { String($0).lowercased() }
        try self.init(words: tokens)
    }

    public var phrase: String {
        words.joined(separator: " ")
    }
}

public enum IdentityError: Error, Equatable {
    case invalidMnemonicLength(Int)
    case wordlistMissing
    case keyDerivationFailed
}

public enum Identity {
    /// PBKDF2(HMAC-SHA512, 100k 轮, 64 字节 seed). 跟 PC 端 ``mnemonic_to_master_key`` 等价.
    public static func masterSeed(from mnemonic: Mnemonic, passphrase: String = "") throws -> Data {
        let phrase = Data(mnemonic.phrase.utf8)
        let salt = Data(("mnemonic" + passphrase).utf8)
        return try pbkdf2HmacSha512(password: phrase, salt: salt, rounds: 100_000, outLen: 64)
    }

    /// 派生 32 字节子密钥 (vault encryption 用). HMAC-SHA256(master, purpose||index).
    public static func deriveSubkey(masterSeed: Data, purpose: String, index: UInt32 = 0) -> Data {
        var info = Data(purpose.utf8)
        var idxBE = index.bigEndian
        withUnsafeBytes(of: &idxBE) { info.append(contentsOf: $0) }
        let key = SymmetricKey(data: masterSeed)
        let mac = HMAC<SHA256>.authenticationCode(for: info, using: key)
        return Data(mac)
    }

    /// DID fingerprint (sha256(masterSeed) 前 16 字节, base32 - 12 字符).
    public static func didFingerprint(masterSeed: Data) -> String {
        let hash = SHA256.hash(data: masterSeed)
        let prefix = Data(hash).prefix(8)
        return prefix.map { String(format: "%02x", $0) }.joined()
    }
}

// 简化 PBKDF2 实现 (CommonCrypto via CryptoKit wrapper).
import CommonCrypto

func pbkdf2HmacSha512(password: Data, salt: Data, rounds: Int, outLen: Int) throws -> Data {
    var out = Data(count: outLen)
    let status = out.withUnsafeMutableBytes { outPtr -> Int32 in
        password.withUnsafeBytes { pwPtr in
            salt.withUnsafeBytes { saltPtr in
                CCKeyDerivationPBKDF(
                    CCPBKDFAlgorithm(kCCPBKDF2),
                    pwPtr.bindMemory(to: Int8.self).baseAddress, password.count,
                    saltPtr.bindMemory(to: UInt8.self).baseAddress, salt.count,
                    CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA512),
                    UInt32(rounds),
                    outPtr.bindMemory(to: UInt8.self).baseAddress, outLen
                )
            }
        }
    }
    if status != kCCSuccess {
        throw IdentityError.keyDerivationFailed
    }
    return out
}
