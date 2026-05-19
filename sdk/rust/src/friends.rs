//! Friends API.

use crate::client::SisoulClient;
use crate::errors::{Result, SisoulError};
use crate::types::{
    Friend, FriendAddRequest, FriendBorrowRequest, FriendLendRequest, LeaseResponse,
    ListFriendsResponse,
};

pub struct FriendsAPI<'a> {
    c: &'a SisoulClient,
}

impl<'a> FriendsAPI<'a> {
    pub fn new(c: &'a SisoulClient) -> Self {
        Self { c }
    }

    pub fn list(&self) -> Result<Vec<Friend>> {
        let r: ListFriendsResponse = self.c.get_json("/friend/list", &[], false)?;
        Ok(r.friends)
    }

    pub fn add(&self, req: &FriendAddRequest) -> Result<Friend> {
        if req.did.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.add: did required".into()));
        }
        self.c.post_json("/friend/add", req, false)
    }

    pub fn remove(&self, did: &str) -> Result<()> {
        if did.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.remove: did required".into()));
        }
        self.c
            .post_unit("/friend/remove", &serde_json::json!({ "did": did }), false)
    }

    pub fn lend(&self, req: &FriendLendRequest) -> Result<LeaseResponse> {
        if req.friend_did.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.lend: friend_did required".into()));
        }
        if req.resource_id.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.lend: resource_id required".into()));
        }
        self.c.post_json("/friend/lend", req, false)
    }

    pub fn borrow(&self, req: &FriendBorrowRequest) -> Result<LeaseResponse> {
        if req.owner_did.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.borrow: owner_did required".into()));
        }
        if req.resource_id.is_empty() {
            return Err(SisoulError::InvalidArgument("friends.borrow: resource_id required".into()));
        }
        self.c.post_json("/friend/borrow", req, false)
    }

    pub fn strong_ties(&self, threshold: f64) -> Result<Vec<Friend>> {
        let all = self.list()?;
        Ok(all.into_iter().filter(|f| f.trust_level >= threshold).collect())
    }
}
