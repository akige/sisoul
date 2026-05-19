import SwiftUI
import SisoulCore

struct VaultView: View {
    @State private var entries: [String] = []
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                ForEach(entries, id: \.self) { e in Text(e).font(.system(.body, design: .monospaced)) }
            }
            .navigationTitle("Vault")
            .toolbar { Button(action: refresh) { Image(systemName: "arrow.clockwise") } }
            .alert("error", isPresented: .constant(error != nil), actions: {
                Button("ok", role: .cancel) { error = nil }
            }, message: { Text(error ?? "") })
        }
        .task { refresh() }
    }

    func refresh() {
        Task {
            do {
                struct Resp: Codable { let items: [Item] }
                struct Item: Codable { let key: String; let updated_at: String }
                let r: Resp = try await DaemonClient().get("/sisoul/preferences/list", as: Resp.self)
                entries = r.items.map { "\($0.key) @ \($0.updated_at)" }
            } catch {
                self.error = String(describing: error)
            }
        }
    }
}
