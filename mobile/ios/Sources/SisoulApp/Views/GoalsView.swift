import SwiftUI
import SisoulCore

struct GoalsView: View {
    @State private var goals: [Goal] = []
    struct Goal: Codable, Identifiable { let id: String; let title: String; let progress: Double }
    var body: some View {
        NavigationStack {
            List(goals) { g in
                VStack(alignment: .leading) {
                    Text(g.title).bold()
                    ProgressView(value: g.progress)
                }
            }
            .navigationTitle("Goals")
        }.task {
            struct Resp: Codable { let goals: [Goal] }
            if let r: Resp = try? await DaemonClient().get("/sisoul/goals/list", as: Resp.self) {
                goals = r.goals
            }
        }
    }
}
