package io.sisoul

import io.sisoul.core.SisoulError
import io.sisoul.core.Vault
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class VaultTest {
    private fun makeKey(): ByteArray = ByteArray(32) { (it and 0xff).toByte() }

    @Test fun roundtrip() {
        val key = makeKey()
        val plain = "hello sisoul world".toByteArray()
        val ct = Vault.encrypt(plain, key)
        val pt = Vault.decrypt(ct, key)
        assertArrayEquals(plain, pt)
    }

    @Test fun headerHasMagic() {
        val key = makeKey()
        val ct = Vault.encrypt("x".toByteArray(), key)
        val magic = String(ct.copyOfRange(0, 8))
        assertEquals("SISOUL01", magic)
    }

    @Test fun wrongKeyThrows() {
        val key1 = makeKey()
        val key2 = ByteArray(32) { ((it + 1) and 0xff).toByte() }
        val ct = Vault.encrypt("secret".toByteArray(), key1)
        assertThrows(SisoulError.DecryptError::class.java) { Vault.decrypt(ct, key2) }
    }

    @Test fun shortBlobRejected() {
        assertThrows(SisoulError.MalformedError::class.java) {
            Vault.decrypt(ByteArray(10), makeKey())
        }
    }

    @Test fun keySizeEnforced() {
        assertThrows(IllegalArgumentException::class.java) {
            Vault.encrypt("x".toByteArray(), ByteArray(16))
        }
    }

    @Test fun magicMismatchRejected() {
        var bad = "WRONG__1".toByteArray() + ByteArray(40)
        assertThrows(SisoulError.MalformedError::class.java) {
            Vault.decrypt(bad, makeKey())
        }
    }
}
