import XCTest
@testable import SisoulCore

final class VaultTests: XCTestCase {
    func testVaultHeaderEncodeDecode() throws {
        let nonce = Data(repeating: 0xAB, count: 12)
        let h = VaultHeader(nonce: nonce)
        let encoded = h.encode()
        XCTAssertEqual(encoded.count, VaultHeader.headerSize)
    }

    func testVaultHeaderRoundtrip() throws {
        let nonce = Data(repeating: 0x42, count: 12)
        let h = VaultHeader(nonce: nonce)
        var blob = h.encode()
        blob.append(Data(repeating: 0, count: 16))
        let (decoded, _) = try VaultHeader.decode(blob)
        XCTAssertEqual(decoded.version, 1)
        XCTAssertEqual(decoded.nonce, nonce)
    }

    func testVaultEntrySerializeParse() throws {
        let fm = Frontmatter(id: "test-1", createdAt: "2026-05-19T10:00:00Z", updatedAt: "2026-05-19T10:00:00Z", kind: "pref", tags: ["alpha"], canary: "sisoul-canary-v1")
        let entry = VaultEntry(frontmatter: fm, body: "hello world")
        let serialized = entry.serialize()
        let parsed = try VaultEntry.parse(serialized)
        XCTAssertEqual(parsed.frontmatter.id, "test-1")
        XCTAssertEqual(parsed.body, "hello world")
    }

    func testCanaryPreserved() throws {
        let fm = Frontmatter(id: "x", createdAt: "t", updatedAt: "t", kind: "k")
        XCTAssertEqual(fm.canary, "sisoul-canary-v1")
    }

    func testMalformedRejected() {
        XCTAssertThrowsError(try VaultHeader.decode(Data(repeating: 0, count: 10)))
    }

    func testWrongMagicRejected() {
        var bad = Data("WRONG__1".utf8)
        bad.append(Data(repeating: 0, count: 40))
        XCTAssertThrowsError(try VaultHeader.decode(bad))
    }
}
