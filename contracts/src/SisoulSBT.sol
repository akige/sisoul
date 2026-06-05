// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @title SisoulSBT — Soulbound Badge for alpha/beta/stable cohort participation.
/// @notice 0 economic value. Non-transferable per whitepaper §4.10 (never-token).
///         Records: holder did_key, cohort label, contribution signature.
/// @dev Soulbound = transfer disabled. Minter role granted to Foundation multisig.
contract SisoulSBT is ERC721, AccessControl {
    bytes32 public constant MINTER_ROLE = keccak256("MINTER_ROLE");

    /// @notice Cohort labels: "alpha-2026", "beta-2026", "stable-2027"...
    mapping(uint256 => string) public cohortOf;
    /// @notice Contribution signature: bytes32 hash of (cases_count, friends_count, chats_sent, prs_merged).
    mapping(uint256 => bytes32) public contributionOf;
    /// @notice did:key string of the badge holder (immutable record).
    mapping(uint256 => string) public didKeyOf;

    uint256 public nextTokenId;

    event BadgeMinted(
        address indexed to,
        uint256 indexed tokenId,
        string cohort,
        string didKey,
        bytes32 contributionSig
    );

    error SoulboundNonTransferable();

    constructor(address admin, address initialMinter)
        ERC721("Sisoul Soulbound Badge", "SISBT")
    {
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(MINTER_ROLE, initialMinter);
    }

    function mintBadge(
        address to,
        string calldata cohort,
        string calldata didKey,
        bytes32 contributionSig
    ) external onlyRole(MINTER_ROLE) returns (uint256 tokenId) {
        tokenId = nextTokenId++;
        _safeMint(to, tokenId);
        cohortOf[tokenId] = cohort;
        didKeyOf[tokenId] = didKey;
        contributionOf[tokenId] = contributionSig;
        emit BadgeMinted(to, tokenId, cohort, didKey, contributionSig);
    }

    // ── Soulbound: disable all transfer paths ──
    // OZ v5 ERC721 routes all transfers through _update().
    function _update(address to, uint256 tokenId, address auth)
        internal
        override
        returns (address)
    {
        address from = _ownerOf(tokenId);
        // Allow mint (from == 0) and burn (to == 0). Disallow transfer.
        if (from != address(0) && to != address(0)) {
            revert SoulboundNonTransferable();
        }
        return super._update(to, tokenId, auth);
    }

    function supportsInterface(bytes4 interfaceId)
        public view override(ERC721, AccessControl) returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }
}
