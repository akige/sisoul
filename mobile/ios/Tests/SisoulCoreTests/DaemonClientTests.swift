import XCTest
@testable import SisoulCore

final class DaemonClientTests: XCTestCase {
    func testBaseURLDefault() {
        let c = DaemonClient()
        XCTAssertEqual(c.baseURL.absoluteString, "http://127.0.0.1:9876")
    }

    func testBaseURLCustom() {
        let url = URL(string: "http://192.168.1.10:9876")!
        let c = DaemonClient(baseURL: url)
        XCTAssertEqual(c.baseURL, url)
    }

    func testHTTPErrorMessage() {
        let err = DaemonClient.ClientError.http(404, "not found")
        if case .http(let code, _) = err { XCTAssertEqual(code, 404) }
        else { XCTFail("wrong error type") }
    }

    func testTimeoutConfig() {
        let c = DaemonClient(timeout: 30)
        XCTAssertEqual(c.timeout, 30)
    }
}
