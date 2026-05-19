// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title PIPRegistry — sisoul PIP (Protocol Improvement Proposal) on-chain registry.
/// @notice PIP-001 ~ PIP-004 (现存) + 未来 PIP. spec 文档 hash (IPFS / Arweave CID) 上链, 状态机管理.
/// @dev 状态: Draft → Review → FinalCall → Final | Withdrawn | Superseded.
///       只有 PIP_ADMIN_ROLE 能改状态; 通常 PIP_ADMIN_ROLE = TimelockController (DAO 控制).
contract PIPRegistry is AccessControl {
    bytes32 public constant PIP_ADMIN_ROLE = keccak256("PIP_ADMIN_ROLE");

    enum Status {
        None,        // 0 sentinel: 不存在
        Draft,       // 1
        Review,      // 2
        FinalCall,   // 3
        Final,       // 4
        Withdrawn,   // 5
        Superseded   // 6
    }

    struct PIP {
        uint256 id;             // PIP 编号 (1-based, 1=PIP-001)
        string title;           // 一句话标题
        string specCID;         // IPFS / Arweave content id (spec 全文 hash)
        address author;         // 提案人 EOA / DID-bound 地址
        Status status;          // 当前状态
        uint64 createdAt;       // block.timestamp
        uint64 updatedAt;       // 状态最近一次变更 ts
        uint256 supersededBy;   // != 0 时表示被该 id 取代; 0 = 否
    }

    /// pipId -> PIP. id 从 1 起 (PIP-001).
    mapping(uint256 => PIP) private _pips;
    /// 已注册 id 列表 (便于枚举).
    uint256[] private _ids;
    /// 防重 id.
    mapping(uint256 => bool) private _registered;

    // ── 事件 ─────────────────────────────────────────────────────────────────

    event PIPRegistered(uint256 indexed id, address indexed author, string title, string specCID);
    event PIPStatusChanged(uint256 indexed id, Status oldStatus, Status newStatus);
    event PIPSpecUpdated(uint256 indexed id, string oldCID, string newCID);
    event PIPSuperseded(uint256 indexed oldId, uint256 indexed newId);

    // ── 错误 ─────────────────────────────────────────────────────────────────

    error PIPAlreadyRegistered(uint256 id);
    error PIPNotFound(uint256 id);
    error PIPInvalidId();
    error PIPInvalidStatus();
    error PIPInvalidTransition(Status from, Status to);

    constructor(address admin) {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(PIP_ADMIN_ROLE, admin);
    }

    // ── write ────────────────────────────────────────────────────────────────

    /// @notice 注册新 PIP. 任何人可注册自己作 author 的 Draft.
    function registerPIP(uint256 id, string calldata title, string calldata specCID) external {
        if (id == 0) revert PIPInvalidId();
        if (_registered[id]) revert PIPAlreadyRegistered(id);
        _pips[id] = PIP({
            id: id,
            title: title,
            specCID: specCID,
            author: msg.sender,
            status: Status.Draft,
            createdAt: uint64(block.timestamp),
            updatedAt: uint64(block.timestamp),
            supersededBy: 0
        });
        _registered[id] = true;
        _ids.push(id);
        emit PIPRegistered(id, msg.sender, title, specCID);
    }

    /// @notice 改 PIP 状态 (仅 PIP_ADMIN_ROLE).
    function setStatus(uint256 id, Status newStatus) external onlyRole(PIP_ADMIN_ROLE) {
        if (!_registered[id]) revert PIPNotFound(id);
        if (newStatus == Status.None) revert PIPInvalidStatus();
        Status old = _pips[id].status;
        if (!_isValidTransition(old, newStatus)) revert PIPInvalidTransition(old, newStatus);
        _pips[id].status = newStatus;
        _pips[id].updatedAt = uint64(block.timestamp);
        emit PIPStatusChanged(id, old, newStatus);
    }

    /// @notice 改 spec CID (作者 / PIP_ADMIN_ROLE). Draft / Review 状态可改.
    function updateSpecCID(uint256 id, string calldata newCID) external {
        if (!_registered[id]) revert PIPNotFound(id);
        PIP storage p = _pips[id];
        require(
            msg.sender == p.author || hasRole(PIP_ADMIN_ROLE, msg.sender),
            "PIPRegistry: not author/admin"
        );
        require(
            p.status == Status.Draft || p.status == Status.Review,
            "PIPRegistry: spec frozen after FinalCall"
        );
        string memory oldCID = p.specCID;
        p.specCID = newCID;
        p.updatedAt = uint64(block.timestamp);
        emit PIPSpecUpdated(id, oldCID, newCID);
    }

    /// @notice 标 oldId 被 newId 取代 (仅 PIP_ADMIN_ROLE).
    function supersede(uint256 oldId, uint256 newId) external onlyRole(PIP_ADMIN_ROLE) {
        if (!_registered[oldId]) revert PIPNotFound(oldId);
        if (!_registered[newId]) revert PIPNotFound(newId);
        Status oldStatus = _pips[oldId].status;
        _pips[oldId].supersededBy = newId;
        _pips[oldId].status = Status.Superseded;
        _pips[oldId].updatedAt = uint64(block.timestamp);
        emit PIPStatusChanged(oldId, oldStatus, Status.Superseded);
        emit PIPSuperseded(oldId, newId);
    }

    // ── read ─────────────────────────────────────────────────────────────────

    function getPIP(uint256 id) external view returns (PIP memory) {
        if (!_registered[id]) revert PIPNotFound(id);
        return _pips[id];
    }

    function getStatus(uint256 id) external view returns (Status) {
        if (!_registered[id]) revert PIPNotFound(id);
        return _pips[id].status;
    }

    function isRegistered(uint256 id) external view returns (bool) {
        return _registered[id];
    }

    function totalPIPs() external view returns (uint256) {
        return _ids.length;
    }

    function pipIdAt(uint256 idx) external view returns (uint256) {
        require(idx < _ids.length, "PIPRegistry: index oob");
        return _ids[idx];
    }

    function allIds() external view returns (uint256[] memory) {
        return _ids;
    }

    // ── internal ─────────────────────────────────────────────────────────────

    /// @dev allowed transitions:
    ///   Draft → Review | Withdrawn
    ///   Review → FinalCall | Draft | Withdrawn
    ///   FinalCall → Final | Review | Withdrawn
    ///   Final → Superseded (only via supersede())
    ///   Withdrawn / Superseded → terminal
    function _isValidTransition(Status from, Status to) internal pure returns (bool) {
        if (from == Status.Draft) {
            return to == Status.Review || to == Status.Withdrawn;
        }
        if (from == Status.Review) {
            return to == Status.FinalCall || to == Status.Draft || to == Status.Withdrawn;
        }
        if (from == Status.FinalCall) {
            return to == Status.Final || to == Status.Review || to == Status.Withdrawn;
        }
        // Final / Withdrawn / Superseded 直接 setStatus 不允许 (走 supersede()).
        return false;
    }
}
