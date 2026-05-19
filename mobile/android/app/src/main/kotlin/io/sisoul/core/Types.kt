package io.sisoul.core

import kotlinx.serialization.Serializable

@Serializable
data class Frontmatter(
    val id: String,
    val createdAt: String,
    val updatedAt: String,
    val kind: String,
    val tags: List<String> = emptyList(),
    val canary: String = "sisoul-canary-v1",
)

@Serializable
data class VaultEntry(
    val frontmatter: Frontmatter,
    val body: String,
)

@Serializable
data class DaemonStatus(
    val version: String,
    val identityFingerprint: String,
    val vaultEntries: Int,
    val lastSync: String? = null,
)

@Serializable
data class SyncRequest(
    val since: String? = null,
    val deviceID: String,
    val pushEntries: List<String> = emptyList(),
)

@Serializable
data class SyncResponse(
    val pulledEntries: List<String>,
    val serverTime: String,
    val conflicts: List<String> = emptyList(),
)

sealed class SisoulError : Exception() {
    data class HttpError(val code: Int, val responseBody: String) : SisoulError() {
        override val message: String get() = "http $code: $responseBody"
    }
    data class NetworkError(val cause: Throwable) : SisoulError() {
        override val message: String get() = "network: ${cause.message}"
    }
    data class DecryptError(val reason: String) : SisoulError() {
        override val message: String get() = "decrypt: $reason"
    }
    data class MalformedError(val reason: String) : SisoulError() {
        override val message: String get() = "malformed: $reason"
    }
}
