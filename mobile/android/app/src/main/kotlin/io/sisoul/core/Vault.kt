package io.sisoul.core

import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Vault 加密. 跟 iOS CryptoKit ChaChaPoly 同步设计 ↔ PC libsodium SecretBox.
 *
 * On-disk layout:
 *   magic(8)="SISOUL01" | version(1)=0x01 | algo(1)=0x01 (AES-GCM/ChaChaPoly) |
 *   reserved(2) | nonce(12) | ciphertext+tag(N+16)
 *
 * Android 用 AES/GCM/NoPadding (硬件加速). algo=0x02. iOS 用 ChaChaPoly algo=0x01.
 * 同 key 跨平台读: TODO Phase 5 加 AES↔ChaCha key 派生兼容 (本 skeleton 各端独立).
 */
object Vault {
    private const val MAGIC = "SISOUL01"
    const val HEADER_SIZE = 24
    const val NONCE_SIZE = 12
    const val TAG_SIZE = 16
    const val ALGO_AES_GCM: Byte = 0x02

    fun encrypt(plaintext: ByteArray, key: ByteArray): ByteArray {
        require(key.size == 32) { "key must be 32 bytes (256-bit)" }
        val nonce = ByteArray(NONCE_SIZE).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_SIZE * 8, nonce))
        val ct = cipher.doFinal(plaintext)
        return buildHeader(nonce) + ct
    }

    fun decrypt(blob: ByteArray, key: ByteArray): ByteArray {
        require(key.size == 32) { "key must be 32 bytes (256-bit)" }
        if (blob.size < HEADER_SIZE + TAG_SIZE) {
            throw SisoulError.MalformedError("blob too short")
        }
        val magic = String(blob.copyOfRange(0, 8))
        if (magic != MAGIC) throw SisoulError.MalformedError("magic mismatch")
        val nonce = blob.copyOfRange(12, 24)
        val ct = blob.copyOfRange(24, blob.size)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_SIZE * 8, nonce))
        return try {
            cipher.doFinal(ct)
        } catch (e: javax.crypto.AEADBadTagException) {
            throw SisoulError.DecryptError("authentication failed: ${e.message}")
        }
    }

    private fun buildHeader(nonce: ByteArray): ByteArray {
        val h = ByteArray(HEADER_SIZE)
        System.arraycopy(MAGIC.toByteArray(), 0, h, 0, 8)
        h[8] = 0x01 // version
        h[9] = ALGO_AES_GCM
        // reserved 10, 11 = 0
        System.arraycopy(nonce, 0, h, 12, NONCE_SIZE)
        return h
    }
}
