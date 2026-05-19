package io.sisoul.core

import org.bouncycastle.crypto.PBEParametersGenerator
import org.bouncycastle.crypto.digests.SHA512Digest
import org.bouncycastle.crypto.generators.PKCS5S2ParametersGenerator
import org.bouncycastle.crypto.params.KeyParameter
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * BIP-39 mnemonic + PBKDF2 master seed 派生. 跟 PC sisoul.identity.seed +
 * iOS Identity.swift 对齐. 12 / 24 词 mnemonic 校验; 完整 wordlist 嵌入需
 * Phase 5 加资源 (skeleton 暂只验长度 + 派生).
 */
data class Mnemonic(val words: List<String>) {
    companion object {
        fun parse(joined: String): Mnemonic {
            val toks = joined.split(Regex("\\s+")).filter { it.isNotEmpty() }.map { it.lowercase() }
            require(toks.size == 12 || toks.size == 24) { "mnemonic must be 12 or 24 words, got ${toks.size}" }
            return Mnemonic(toks)
        }
    }
    val phrase: String get() = words.joined(" ")
    private fun List<String>.joined(sep: String) = joinToString(sep)
}

object Identity {
    /** PBKDF2(HMAC-SHA512, 100k 轮, 64 字节 seed). */
    fun masterSeed(mnemonic: Mnemonic, passphrase: String = ""): ByteArray {
        val pw = PBEParametersGenerator.PKCS5PasswordToUTF8Bytes(mnemonic.phrase.toCharArray())
        val salt = ("mnemonic$passphrase").toByteArray(Charsets.UTF_8)
        val gen = PKCS5S2ParametersGenerator(SHA512Digest()).apply { init(pw, salt, 100_000) }
        return (gen.generateDerivedParameters(64 * 8) as KeyParameter).key
    }

    /** HMAC-SHA256(masterSeed, purpose||indexBE) → 32B 子密钥. */
    fun deriveSubkey(masterSeed: ByteArray, purpose: String, index: Int = 0): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(masterSeed, "HmacSHA256"))
        mac.update(purpose.toByteArray(Charsets.UTF_8))
        // big-endian uint32
        mac.update(byteArrayOf(
            ((index shr 24) and 0xff).toByte(),
            ((index shr 16) and 0xff).toByte(),
            ((index shr 8) and 0xff).toByte(),
            (index and 0xff).toByte(),
        ))
        return mac.doFinal()
    }

    /** DID fingerprint = sha256(masterSeed) 前 8 字节 hex. */
    fun didFingerprint(masterSeed: ByteArray): String {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        val h = md.digest(masterSeed).copyOfRange(0, 8)
        return h.joinToString("") { "%02x".format(it) }
    }
}
