import Foundation

// HTTP client 调本机 sisoul daemon (默认 127.0.0.1:9876).
// PC daemon 在同 LAN / hotspot 时 baseURL 改对应 IP.
//
// 路径跟 src/sisoul/daemon.py 内 68 endpoints 对齐:
//   GET  /sisoul/identity
//   GET  /sisoul/preferences/list
//   GET  /sisoul/goals/list
//   GET  /sisoul/friend/list
//   GET  /sisoul/skill/list
//   POST /sisoul/sync/push
//   ...

public struct DaemonClient {
    public let baseURL: URL
    public let session: URLSession
    public let timeout: TimeInterval

    public init(baseURL: URL = URL(string: "http://127.0.0.1:9876")!,
                session: URLSession = .shared,
                timeout: TimeInterval = 10) {
        self.baseURL = baseURL
        self.session = session
        self.timeout = timeout
    }

    public enum ClientError: Error {
        case http(Int, String)
        case network(Error)
        case decode(Error)
    }

    public func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.timeoutInterval = timeout
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        return try await execute(req, as: type)
    }

    public func post<Body: Encodable, T: Decodable>(_ path: String, body: Body, as type: T.Type) async throws -> T {
        var req = URLRequest(url: baseURL.appendingPathComponent(path))
        req.httpMethod = "POST"
        req.timeoutInterval = timeout
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let encoder = JSONEncoder()
        req.httpBody = try encoder.encode(body)
        return try await execute(req, as: type)
    }

    private func execute<T: Decodable>(_ req: URLRequest, as type: T.Type) async throws -> T {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw ClientError.network(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw ClientError.http(0, "no http response")
        }
        if http.statusCode >= 400 {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw ClientError.http(http.statusCode, body)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw ClientError.decode(error)
        }
    }

    // 便捷 API
    public func status() async throws -> DaemonStatus {
        try await get("/sisoul/status", as: DaemonStatus.self)
    }

    public func sync(_ req: SyncRequest) async throws -> SyncResponse {
        try await post("/sisoul/sync/push", body: req, as: SyncResponse.self)
    }
}
