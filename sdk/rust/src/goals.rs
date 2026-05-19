//! Goals API.

use crate::client::SisoulClient;
use crate::errors::{Result, SisoulError};
use crate::types::{Goal, GoalCreateRequest, GoalUpdateRequest, ListGoalsResponse};

pub struct GoalsAPI<'a> {
    c: &'a SisoulClient,
}

impl<'a> GoalsAPI<'a> {
    pub fn new(c: &'a SisoulClient) -> Self {
        Self { c }
    }

    pub fn list(&self) -> Result<Vec<Goal>> {
        let r: ListGoalsResponse = self.c.get_json("/goals/list", &[], false)?;
        Ok(r.goals)
    }

    pub fn add(&self, req: &GoalCreateRequest) -> Result<Goal> {
        if req.title.is_empty() {
            return Err(SisoulError::InvalidArgument("goals.add: title required".into()));
        }
        self.c.post_json("/goals/add", req, false)
    }

    pub fn update(&self, req: &GoalUpdateRequest) -> Result<Goal> {
        if req.id.is_empty() {
            return Err(SisoulError::InvalidArgument("goals.update: id required".into()));
        }
        self.c.post_json("/goals/update", req, false)
    }

    pub fn delete(&self, id: &str) -> Result<()> {
        if id.is_empty() {
            return Err(SisoulError::InvalidArgument("goals.delete: id required".into()));
        }
        self.c.post_unit("/goals/delete", &serde_json::json!({ "id": id }), false)
    }

    pub fn bump_progress(&self, id: &str, delta: f64) -> Result<Goal> {
        let goals = self.list()?;
        let target = goals
            .iter()
            .find(|g| g.id == id)
            .ok_or_else(|| SisoulError::InvalidArgument(format!("goal {} not found", id)))?;
        let nxt = (target.progress + delta).clamp(0.0, 1.0);
        self.update(&GoalUpdateRequest {
            id: id.to_string(),
            progress: Some(nxt),
            ..Default::default()
        })
    }
}
