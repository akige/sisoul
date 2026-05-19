import SwiftUI
import SisoulCore

struct FriendsView: View {
    @State private var friends: [Friend] = []
    struct Friend: Codable, Identifiable { let did: String; var id: String { did }; let handle: String?; let trust_level: Int }
    var body: some View {
        NavigationStack {
            List(friends) { f in
                VStack(alignment: .leading) {
                    Text(f.handle ?? f.did).bold()
                    Text("trust=\(f.trust_level)").font(.caption)
                }
            }
            .navigationTitle("Friends")
        }.task {
            struct Resp: Codable { let friends: [Friend] }
            if let r: Resp = try? await DaemonClient().get("/sisoul/friend/list", as: Resp.self) {
                friends = r.friends
            }
        }
    }
}
