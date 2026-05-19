import XCTest
@testable import SisoulCore

final class IdentityTests: XCTestCase {
    func testMnemonicLength12() throws {
        let m = try Mnemonic(joined: "a b c d e f g h i j k l")
        XCTAssertEqual(m.words.count, 12)
    }

    func testMnemonicInvalidLength() {
        XCTAssertThrowsError(try Mnemonic(words: ["a", "b", "c"]))
    }

    func testMasterSeedDeterministic() throws {
        let m = try Mnemonic(words: Array(repeating: "abandon", count: 12))
        let s1 = try Identity.masterSeed(from: m)
        let s2 = try Identity.masterSeed(from: m)
        XCTAssertEqual(s1, s2)
        XCTAssertEqual(s1.count, 64)
    }

    func testDeriveSubkeyDifferentPurpose() throws {
        let m = try Mnemonic(words: Array(repeating: "abandon", count: 12))
        let seed = try Identity.masterSeed(from: m)
        let k1 = Identity.deriveSubkey(masterSeed: seed, purpose: "vault")
        let k2 = Identity.deriveSubkey(masterSeed: seed, purpose: "did")
        XCTAssertNotEqual(k1, k2)
    }

    func testFingerprintStable() throws {
        let m = try Mnemonic(words: Array(repeating: "abandon", count: 12))
        let seed = try Identity.masterSeed(from: m)
        let fp1 = Identity.didFingerprint(masterSeed: seed)
        let fp2 = Identity.didFingerprint(masterSeed: seed)
        XCTAssertEqual(fp1, fp2)
        XCTAssertEqual(fp1.count, 16) // 8 bytes = 16 hex chars
    }
}
