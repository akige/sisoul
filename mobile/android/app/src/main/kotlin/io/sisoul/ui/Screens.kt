package io.sisoul.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable fun VaultScreen() {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Vault", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text("加密 vault entries · sisoul-canary-v1 protected")
        // TODO Phase 5: list real entries via DaemonClient
    }
}

@Composable fun GoalsScreen() {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Goals", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text("长期目标管理 · daemon scheduler 后台审进度")
    }
}

@Composable fun FriendsScreen() {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Friends", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text("DID + EAS 双向 attestation · 3 档授权")
    }
}

@Composable fun SkillsScreen() {
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Skills", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text("AI 技能 packaging + 30/60/120min 借用")
    }
}

@Composable fun SettingsScreen() {
    var daemonURL by remember { mutableStateOf("http://127.0.0.1:9876") }
    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Settings", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(16.dp))
        OutlinedTextField(value = daemonURL, onValueChange = { daemonURL = it }, label = { Text("Daemon URL") })
        Spacer(Modifier.height(16.dp))
        Text("Sisoul 1.0.0+internal")
        Text("Decentralized AI meta-layer")
    }
}
