import SwiftUI
import SisoulCore

struct SettingsView: View {
    @State private var daemonURL: String = "http://127.0.0.1:9876"
    @State private var did: String = ""
    var body: some View {
        NavigationStack {
            Form {
                Section("Daemon") {
                    TextField("URL", text: $daemonURL)
                }
                Section("Identity") {
                    Text(did.isEmpty ? "(loading)" : did).font(.system(.caption, design: .monospaced))
                }
                Section("About") {
                    Text("Sisoul 1.0.0+internal")
                    Text("Decentralized AI meta-layer")
                }
            }
            .navigationTitle("Settings")
        }.task {
            struct Resp: Codable { let did: String }
            if let r: Resp = try? await DaemonClient(baseURL: URL(string: daemonURL)!).get("/sisoul/identity", as: Resp.self) {
                did = r.did
            }
        }
    }
}
