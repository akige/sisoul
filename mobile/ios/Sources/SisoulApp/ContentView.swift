import SwiftUI

struct ContentView: View {
    var body: some View {
        TabView {
            VaultView()
                .tabItem { Label("Vault", systemImage: "lock.shield") }
            GoalsView()
                .tabItem { Label("Goals", systemImage: "flag") }
            FriendsView()
                .tabItem { Label("Friends", systemImage: "person.2") }
            SkillsView()
                .tabItem { Label("Skills", systemImage: "wand.and.stars") }
            SettingsView()
                .tabItem { Label("Settings", systemImage: "gear") }
        }
    }
}
