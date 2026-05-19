// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Permit} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import {ERC20Votes} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Votes.sol";
import {ERC20Capped} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Capped.sol";
import {Nonces} from "@openzeppelin/contracts/utils/Nonces.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title SisoulToken — ERC20Votes governance token (capped, mint via MINTER_ROLE).
/// @notice Phase 3 P3-4 (sisoul DAO governance). Vesting 通过外部 TimelockController 持有.
/// @dev 1B cap. checkpoints via ERC20Votes for snapshot voting compatibility (Governor 模).
contract SisoulToken is ERC20, ERC20Permit, ERC20Votes, ERC20Capped, AccessControl {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");

    /// @param admin DEFAULT_ADMIN_ROLE 持有者 (一般是 multisig).
    /// @param initialMinter 启动期 mint 权限 (后续 renounce).
    /// @param cap_ 硬上限 (e.g. 1_000_000_000 * 1e18).
    constructor(address admin, address initialMinter, uint256 cap_)
        ERC20("Sisoul Governance Token", "SIS")
        ERC20Permit("Sisoul Governance Token")
        ERC20Capped(cap_)
    {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE, initialMinter);
    }

    function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) {
        _mint(to, amount);
    }

    // ── multiple inheritance resolution (OZ v5.x) ─────────────────────────────

    function _update(address from, address to, uint256 value)
        internal
        override(ERC20, ERC20Votes, ERC20Capped)
    {
        super._update(from, to, value);
    }

    function nonces(address owner) public view override(ERC20Permit, Nonces) returns (uint256) {
        return super.nonces(owner);
    }
}
