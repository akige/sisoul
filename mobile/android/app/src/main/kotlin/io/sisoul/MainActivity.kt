package io.sisoul

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import io.sisoul.ui.VaultScreen
import io.sisoul.ui.GoalsScreen
import io.sisoul.ui.FriendsScreen
import io.sisoul.ui.SkillsScreen
import io.sisoul.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    var tab by remember { mutableStateOf(0) }
                    Column {
                        when (tab) {
                            0 -> VaultScreen()
                            1 -> GoalsScreen()
                            2 -> FriendsScreen()
                            3 -> SkillsScreen()
                            else -> SettingsScreen()
                        }
                    }
                }
            }
        }
    }
}
