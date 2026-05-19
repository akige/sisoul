package io.sisoul.core

import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * HTTP client → 本机 sisoul daemon (127.0.0.1:9876 默认).
 * PC daemon 在同 LAN / hotspot 时 baseURL 改对应 IP. 跟 iOS DaemonClient 对齐.
 */
class DaemonClient(
    val baseURL: String = "http://127.0.0.1:9876",
    val timeoutSec: Long = 10,
) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(timeoutSec, TimeUnit.SECONDS)
        .readTimeout(timeoutSec, TimeUnit.SECONDS)
        .build()

    private val json = Json { ignoreUnknownKeys = true }

    inline fun <reified T> get(path: String): T {
        val req = Request.Builder().url("$baseURL$path").get().build()
        return execute(req)
    }

    inline fun <reified Body, reified T> post(path: String, body: Body): T {
        val payload = Json.encodeToString(body)
        val req = Request.Builder()
            .url("$baseURL$path")
            .post(payload.toRequestBody("application/json".toMediaType()))
            .build()
        return execute(req)
    }

    inline fun <reified T> execute(req: Request): T {
        val resp = try { http.newCall(req).execute() } catch (e: Exception) {
            throw SisoulError.NetworkError(e)
        }
        resp.use { r ->
            val body = r.body?.string() ?: ""
            if (!r.isSuccessful) throw SisoulError.HttpError(r.code, body)
            return Json { ignoreUnknownKeys = true }.decodeFromString(body)
        }
    }

    fun status(): DaemonStatus = get("/sisoul/status")
    fun sync(req: SyncRequest): SyncResponse = post("/sisoul/sync/push", req)
}
