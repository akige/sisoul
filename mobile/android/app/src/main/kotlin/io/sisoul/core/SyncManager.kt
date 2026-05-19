package io.sisoul.core

import java.util.UUID
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.coroutineScope

/** SyncManager · 跟 PC daemon 双向 sync, 跟 iOS SyncManager 对齐. */
class SyncManager(
    private val client: DaemonClient,
    val deviceID: String = UUID.randomUUID().toString(),
) {
    var lastSyncedAt: String? = null
        private set
    var lastError: Throwable? = null
        private set

    fun syncOnce(pushEntries: List<VaultEntry> = emptyList()): Result<SyncResponse> {
        val pushSerialized = pushEntries.map { kotlinx.serialization.json.Json.encodeToString(it) }
        val req = SyncRequest(since = lastSyncedAt, deviceID = deviceID, pushEntries = pushSerialized)
        return try {
            val resp = client.sync(req)
            lastSyncedAt = resp.serverTime
            lastError = null
            Result.success(resp)
        } catch (e: Throwable) {
            lastError = e
            Result.failure(e)
        }
    }

    suspend fun runAutoSync(intervalMs: Long = 300_000L, scope: CoroutineScope) {
        coroutineScope {
            while (scope.isActive) {
                syncOnce()
                delay(intervalMs)
            }
        }
    }
}
