import SwiftUI
import SisoulCore

struct SkillsView: View {
    @State private var owned: [Skill] = []
    struct Skill: Codable, Identifiable { let skill_id: String; var id: String { skill_id }; let name: String; let version: String }
    var body: some View {
        NavigationStack {
            List(owned) { s in
                HStack { Text(s.name); Spacer(); Text(s.version).font(.system(.caption, design: .monospaced)) }
            }
            .navigationTitle("Skills")
        }.task {
            struct Resp: Codable { let owned: [Skill] }
            if let r: Resp = try? await DaemonClient().get("/sisoul/skill/list", as: Resp.self) {
                owned = r.owned
            }
        }
    }
}
