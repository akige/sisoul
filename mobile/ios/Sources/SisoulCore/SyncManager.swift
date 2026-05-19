import Foundation

// SyncManager · 跟 PC daemon 双向 sync + 冲突解决 (last-write-wins 默认).

public actor SyncManager {
    public let client: DaemonClient
    public private(set) var deviceID: String
    public private(set) var lastSyncedAt: Date?
    public private(set) var lastError: Error?

    public init(client: DaemonClient, deviceID: String = UUID().uuidString) {
        self.client = client
        self.deviceID = deviceID
    }

    public func syncOnce(pushEntries: [VaultEntry] = []) async -> Result<SyncResponse, Error> {
        let since = lastSyncedAt.map { ISO8601DateFormatter().string(from: $0) }
        let pushSerialized = pushEntries.map { String(data: $0.serialize(), encoding: .utf8) ?? "" }
        let req = SyncRequest(since: since, deviceID: deviceID, pushEntries: pushSerialized)
        do {
            let resp = try await client.sync(req)
            lastSyncedAt = Date()
            lastError = nil
            return .success(resp)
        } catch {
            lastError = error
            return .failure(error)
        }
    }

    // 自动 sync (interval 秒). 调用方启 Task 包装.
    public func runAutoSync(intervalSeconds: UInt64 = 300) async {
        while !Task.isCancelled {
            _ = await syncOnce()
            try? await Task.sleep(nanoseconds: intervalSeconds * 1_000_000_000)
        }
    }
}
