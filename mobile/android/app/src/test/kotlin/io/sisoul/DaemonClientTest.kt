package io.sisoul

import io.sisoul.core.DaemonClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test

class DaemonClientTest {
    private lateinit var server: MockWebServer

    @BeforeEach fun setup() { server = MockWebServer().also { it.start() } }
    @AfterEach fun teardown() { server.shutdown() }

    @Test fun statusOK() {
        server.enqueue(MockResponse().setBody("""{"version":"1.0.0+internal","identityFingerprint":"abc","vaultEntries":42,"lastSync":null}"""))
        val c = DaemonClient(baseURL = server.url("/").toString().trimEnd('/'))
        val s = c.status()
        assertEquals("1.0.0+internal", s.version)
        assertEquals(42, s.vaultEntries)
    }

    @Test fun http404Throws() {
        server.enqueue(MockResponse().setResponseCode(404).setBody("not found"))
        val c = DaemonClient(baseURL = server.url("/").toString().trimEnd('/'))
        assertThrows(io.sisoul.core.SisoulError.HttpError::class.java) { c.status() }
    }

    @Test fun defaultBaseURL() {
        val c = DaemonClient()
        assertEquals("http://127.0.0.1:9876", c.baseURL)
    }

    @Test fun customTimeout() {
        val c = DaemonClient(timeoutSec = 30)
        assertEquals(30, c.timeoutSec)
    }
}
