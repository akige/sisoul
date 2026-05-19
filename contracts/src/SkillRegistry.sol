// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title SkillRegistry — AI skill on-chain CID registry.
/// @notice 每个 skill = (owner DID hash, IPFS/Arweave CID, version, metadata).
///         skill author 注册自己, 升级版本 = 新 CID. 通过 DAO governance 可冻结 (DAO_ROLE).
contract SkillRegistry is AccessControl {
    /// @dev DAO governance 控权 (一般 = TimelockController). 可 freeze / unfreeze.
    bytes32 public constant DAO_ROLE = keccak256("DAO_ROLE");

    struct Skill {
        bytes32 skillId;        // keccak256(owner DID || slug)
        bytes32 ownerDIDHash;   // keccak256(did:sisoul:<handle>)
        address ownerAddr;      // EOA bound 到 DID (PIP-002 DID binding)
        string slug;            // 短名 (e.g. "code-review-v2")
        string currentCID;      // 当前版本 CID (Arweave/IPFS)
        uint32 version;         // 单调递增
        uint64 createdAt;
        uint64 updatedAt;
        bool frozen;            // DAO 冻结后不许更新
    }

    /// skillId -> Skill
    mapping(bytes32 => Skill) private _skills;
    /// skillId -> 版本历史 CID 列表
    mapping(bytes32 => string[]) private _history;
    /// skillId 注册标记
    mapping(bytes32 => bool) private _registered;
    /// 所有 skillId 枚举
    bytes32[] private _ids;
    /// owner -> 拥有的 skillIds
    mapping(address => bytes32[]) private _byOwner;

    // ── 事件 ─────────────────────────────────────────────────────────────────

    event SkillRegistered(
        bytes32 indexed skillId,
        bytes32 indexed ownerDIDHash,
        address indexed ownerAddr,
        string slug,
        string cid
    );
    event SkillUpdated(bytes32 indexed skillId, uint32 newVersion, string oldCID, string newCID);
    event SkillFrozen(bytes32 indexed skillId, address indexed by);
    event SkillUnfrozen(bytes32 indexed skillId, address indexed by);
    event SkillOwnerTransferred(
        bytes32 indexed skillId, address indexed oldOwner, address indexed newOwner
    );

    // ── 错误 ─────────────────────────────────────────────────────────────────

    error SkillAlreadyRegistered(bytes32 skillId);
    error SkillNotFound(bytes32 skillId);
    error SkillFrozenError(bytes32 skillId);
    error NotSkillOwner(bytes32 skillId);
    error InvalidInput();

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(DAO_ROLE, admin);
    }

    /// @notice 计算 skillId. 同一 owner DID + 不同 slug → 不同 skill.
    function computeSkillId(bytes32 ownerDIDHash, string calldata slug)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(ownerDIDHash, "::", slug));
    }

    // ── write ────────────────────────────────────────────────────────────────

    /// @notice 注册新 skill. msg.sender = ownerAddr.
    function registerSkill(bytes32 ownerDIDHash, string calldata slug, string calldata cid)
        external
        returns (bytes32 skillId)
    {
        if (ownerDIDHash == bytes32(0) || bytes(slug).length == 0 || bytes(cid).length == 0) {
            revert InvalidInput();
        }
        skillId = computeSkillId(ownerDIDHash, slug);
        if (_registered[skillId]) revert SkillAlreadyRegistered(skillId);

        _skills[skillId] = Skill({
            skillId: skillId,
            ownerDIDHash: ownerDIDHash,
            ownerAddr: msg.sender,
            slug: slug,
            currentCID: cid,
            version: 1,
            createdAt: uint64(block.timestamp),
            updatedAt: uint64(block.timestamp),
            frozen: false
        });
        _registered[skillId] = true;
        _ids.push(skillId);
        _byOwner[msg.sender].push(skillId);
        _history[skillId].push(cid);

        emit SkillRegistered(skillId, ownerDIDHash, msg.sender, slug, cid);
    }

    /// @notice 升级版本 CID. 只有 ownerAddr 可调; frozen=true 拒.
    function updateSkill(bytes32 skillId, string calldata newCID) external {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        Skill storage s = _skills[skillId];
        if (s.frozen) revert SkillFrozenError(skillId);
        if (s.ownerAddr != msg.sender) revert NotSkillOwner(skillId);
        if (bytes(newCID).length == 0) revert InvalidInput();

        string memory oldCID = s.currentCID;
        s.currentCID = newCID;
        s.version += 1;
        s.updatedAt = uint64(block.timestamp);
        _history[skillId].push(newCID);

        emit SkillUpdated(skillId, s.version, oldCID, newCID);
    }

    /// @notice DAO 冻结 (e.g. 投票判定 skill 违规).
    function freezeSkill(bytes32 skillId) external onlyRole(DAO_ROLE) {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        _skills[skillId].frozen = true;
        emit SkillFrozen(skillId, msg.sender);
    }

    function unfreezeSkill(bytes32 skillId) external onlyRole(DAO_ROLE) {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        _skills[skillId].frozen = false;
        emit SkillUnfrozen(skillId, msg.sender);
    }

    /// @notice owner 转 skill 控制权 (DID 仍记录原始).
    function transferOwner(bytes32 skillId, address newOwner) external {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        if (newOwner == address(0)) revert InvalidInput();
        Skill storage s = _skills[skillId];
        if (s.ownerAddr != msg.sender) revert NotSkillOwner(skillId);
        address oldOwner = s.ownerAddr;
        s.ownerAddr = newOwner;
        s.updatedAt = uint64(block.timestamp);
        _byOwner[newOwner].push(skillId);
        emit SkillOwnerTransferred(skillId, oldOwner, newOwner);
    }

    // ── read ─────────────────────────────────────────────────────────────────

    function getSkill(bytes32 skillId) external view returns (Skill memory) {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        return _skills[skillId];
    }

    function isRegistered(bytes32 skillId) external view returns (bool) {
        return _registered[skillId];
    }

    function totalSkills() external view returns (uint256) {
        return _ids.length;
    }

    function skillIdAt(uint256 idx) external view returns (bytes32) {
        require(idx < _ids.length, "SkillRegistry: index oob");
        return _ids[idx];
    }

    function skillsByOwner(address owner) external view returns (bytes32[] memory) {
        return _byOwner[owner];
    }

    function historyOf(bytes32 skillId) external view returns (string[] memory) {
        if (!_registered[skillId]) revert SkillNotFound(skillId);
        return _history[skillId];
    }
}
