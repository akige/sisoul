package io.sisoul

import io.sisoul.core.Identity
import io.sisoul.core.Mnemonic
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class IdentityTest {
    @Test fun mnemonicParse12Words() {
        val m = Mnemonic.parse("a b c d e f g h i j k l")
        assertEquals(12, m.words.size)
    }

    @Test fun mnemonicInvalidLengthThrows() {
        assertThrows(IllegalArgumentException::class.java) { Mnemonic.parse("a b c") }
    }

    @Test fun masterSeedDeterministic() {
        val m = Mnemonic(List(12) { "abandon" })
        val s1 = Identity.masterSeed(m)
        val s2 = Identity.masterSeed(m)
        assertArrayEquals(s1, s2)
        assertEquals(64, s1.size)
    }

    @Test fun deriveSubkeyDifferentPurpose() {
        val m = Mnemonic(List(12) { "abandon" })
        val seed = Identity.masterSeed(m)
        val k1 = Identity.deriveSubkey(seed, "vault")
        val k2 = Identity.deriveSubkey(seed, "did")
        assertFalse(k1.contentEquals(k2))
    }

    @Test fun fingerprintStable() {
        val m = Mnemonic(List(12) { "abandon" })
        val seed = Identity.masterSeed(m)
        val fp1 = Identity.didFingerprint(seed)
        val fp2 = Identity.didFingerprint(seed)
        assertEquals(fp1, fp2)
        assertEquals(16, fp1.length)
    }
}
