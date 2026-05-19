// mockito 集成测试 — 覆盖所有子 API + 错误路径
use mockito::Server;
use sisoul_client::{
    GoalCreateRequest, SisoulClient, SisoulError, SkillCreateRequest,
};

fn make_client(server: &Server) -> SisoulClient {
    // base_url 设为 mock server origin + /sisoul, absolute=true 的 /sisoul/* 也能命中.
    SisoulClient::new(format!("{}/sisoul", server.url())).unwrap()
}

#[test]
fn vault_list() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/preferences/list")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"items":[{"key":"theme","value":"dark","updated_at":"2026"}]}"#)
        .create();
    let c = make_client(&s);
    let items = c.vault().list().unwrap();
    assert_eq!(items.len(), 1);
    assert_eq!(items[0].key, "theme");
}

#[test]
fn vault_get_with_param() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/preferences/get")
        .match_query(mockito::Matcher::UrlEncoded("key".into(), "hello world".into()))
        .with_status(200)
        .with_body(r#"{"key":"hello world","value":"v"}"#)
        .create();
    let c = make_client(&s);
    let v = c.vault().get("hello world").unwrap();
    assert_eq!(v.as_deref(), Some("v"));
}

#[test]
fn vault_set_post_body() {
    let mut s = Server::new();
    let _m = s
        .mock("POST", "/sisoul/preferences/set")
        .match_body(mockito::Matcher::JsonString(
            r#"{"key":"a","value":"b"}"#.into(),
        ))
        .with_status(200)
        .with_body(r#"{"ok":true}"#)
        .create();
    let c = make_client(&s);
    c.vault().set("a", "b").unwrap();
}

#[test]
fn vault_rejects_empty_key() {
    let s = Server::new();
    let c = make_client(&s);
    let err = c.vault().set("", "x").unwrap_err();
    matches!(err, SisoulError::InvalidArgument(_));
}

#[test]
fn goals_list() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/goals/list")
        .with_status(200)
        .with_body(r#"{"goals":[{"id":"g1","title":"T","progress":0.5}]}"#)
        .create();
    let c = make_client(&s);
    let g = c.goals().list().unwrap();
    assert_eq!(g[0].id, "g1");
}

#[test]
fn goals_add_requires_title() {
    let s = Server::new();
    let c = make_client(&s);
    let err = c
        .goals()
        .add(&GoalCreateRequest {
            title: "".into(),
            ..Default::default()
        })
        .unwrap_err();
    matches!(err, SisoulError::InvalidArgument(_));
}

#[test]
fn goals_bump_progress_clamps() {
    let mut s = Server::new();
    let _list = s
        .mock("GET", "/sisoul/goals/list")
        .with_status(200)
        .with_body(r#"{"goals":[{"id":"g1","title":"T","progress":0.9}]}"#)
        .create();
    let _upd = s
        .mock("POST", "/sisoul/goals/update")
        .match_body(mockito::Matcher::PartialJsonString(
            r#"{"progress":1.0}"#.into(),
        ))
        .with_status(200)
        .with_body(r#"{"id":"g1","title":"T","progress":1.0}"#)
        .create();
    let c = make_client(&s);
    let out = c.goals().bump_progress("g1", 0.5).unwrap();
    assert!((out.progress - 1.0).abs() < 1e-9);
}

#[test]
fn friends_strong_ties() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/friend/list")
        .with_status(200)
        .with_body(
            r#"{"friends":[
                {"did":"did:1","trust_level":0.8,"connected_at":"x"},
                {"did":"did:2","trust_level":0.3,"connected_at":"x"}
            ]}"#,
        )
        .create();
    let c = make_client(&s);
    let strong = c.friends().strong_ties(0.7).unwrap();
    assert_eq!(strong.len(), 1);
    assert_eq!(strong[0].did, "did:1");
}

#[test]
fn skills_list_absolute_path() {
    let mut s = Server::new();
    // 必须打 /sisoul/skill/list 不能是 /sisoul/sisoul/skill/list
    let _m = s
        .mock("GET", "/sisoul/skill/list")
        .with_status(200)
        .with_body(r#"{"own_did":"did:1","owned":[],"available_to_borrow":[]}"#)
        .create();
    let c = make_client(&s);
    let out = c.skills().list().unwrap();
    assert_eq!(out.own_did, "did:1");
}

#[test]
fn skills_create_validates() {
    let s = Server::new();
    let c = make_client(&s);
    let err = c
        .skills()
        .create(&SkillCreateRequest {
            name: "x".into(),
            description: "".into(),
            system_prompt: "".into(),
            ..Default::default()
        })
        .unwrap_err();
    matches!(err, SisoulError::InvalidArgument(_));
}

#[test]
fn skills_active_sessions_filter() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/skill/sessions")
        .with_status(200)
        .with_body(
            r#"{"own_did":"did:1","sessions":[
            {"session_id":"s1","skill_id":"k","skill_name":"n","qualified_name":"q","owner_did":"o","borrower_did":"b","status":"active","started_at":0,"expires_at":1,"proxy_endpoint":"e","wiped":false},
            {"session_id":"s2","skill_id":"k","skill_name":"n","qualified_name":"q","owner_did":"o","borrower_did":"b","status":"expired","started_at":0,"expires_at":1,"proxy_endpoint":"e","wiped":false}
        ]}"#,
        )
        .create();
    let c = make_client(&s);
    let act = c.skills().active_sessions().unwrap();
    assert_eq!(act.len(), 1);
    assert_eq!(act[0].session_id, "s1");
}

#[test]
fn attest_by_schema() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/attest/history")
        .with_status(200)
        .with_body(
            r#"{"history":[
            {"uid":"1","schema":"skill","timestamp":100,"chain":"op"},
            {"uid":"2","schema":"kyc","timestamp":200,"chain":"op"}
        ]}"#,
        )
        .create();
    let c = make_client(&s);
    let out = c.attest().by_schema("kyc").unwrap();
    assert_eq!(out.len(), 1);
    assert_eq!(out[0].uid, "2");
}

#[test]
fn attest_since() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/attest/history")
        .with_status(200)
        .with_body(
            r#"{"history":[
            {"uid":"1","schema":"x","timestamp":100,"chain":"c"},
            {"uid":"2","schema":"x","timestamp":200,"chain":"c"}
        ]}"#,
        )
        .create();
    let c = make_client(&s);
    let out = c.attest().since(150).unwrap();
    assert_eq!(out.len(), 1);
}

#[test]
fn http_404_yields_daemon_error() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/preferences/list")
        .with_status(404)
        .with_body("nope")
        .create();
    let c = make_client(&s);
    let err = c.vault().list().unwrap_err();
    matches!(err, SisoulError::Daemon { status: 404, .. });
}

#[test]
fn http_401_yields_auth_error() {
    let mut s = Server::new();
    let _m = s
        .mock("GET", "/sisoul/preferences/list")
        .with_status(401)
        .with_body("unauth")
        .create();
    let c = make_client(&s);
    let err = c.vault().list().unwrap_err();
    matches!(err, SisoulError::Auth { status: 401, .. });
}
