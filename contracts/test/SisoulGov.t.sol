// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";
import {IGovernor} from "@openzeppelin/contracts/governance/IGovernor.sol";
import {SisoulToken} from "../src/SisoulToken.sol";
import {SisoulGov} from "../src/SisoulGov.sol";

contract SisoulGovTest is Test {
    SisoulToken internal token;
    TimelockController internal timelock;
    SisoulGov internal gov;

    address internal admin = address(0xA11CE);
    address internal alice = address(0xA1);
    address internal bob = address(0xB0B);
    address internal carol = address(0xCA);
    address internal recipient = address(0xDEAD);

    uint256 internal constant CAP = 1_000_000_000 ether;
    uint256 internal constant ALICE_MINT = 600_000 ether; // 0.06% — quorum 4% 需 40M, 这里只为 propose+self test
    uint256 internal constant BIG_MINT = 50_000_000 ether; // 5%, 过 quorum

    function setUp() public {
        // deployer = admin
        vm.startPrank(admin);

        token = new SisoulToken(admin, admin, CAP);

        address[] memory empty = new address[](0);
        timelock = new TimelockController(2 days, empty, empty, admin);

        gov = new SisoulGov(token, timelock);

        // 授权
        timelock.grantRole(timelock.PROPOSER_ROLE(), address(gov));
        timelock.grantRole(timelock.CANCELLER_ROLE(), address(gov));
        timelock.grantRole(timelock.EXECUTOR_ROLE(), address(0));

        // alice / bob / carol mint + delegate
        token.mint(alice, BIG_MINT); // 5%, 单独够 quorum
        token.mint(bob, BIG_MINT);
        token.mint(carol, ALICE_MINT); // 仅够 proposalThreshold

        vm.stopPrank();

        vm.prank(alice);
        token.delegate(alice);
        vm.prank(bob);
        token.delegate(bob);
        vm.prank(carol);
        token.delegate(carol);

        // 推一秒让 checkpoint 生效
        vm.warp(block.timestamp + 1);
    }

    // ── helpers ───────────────────────────────────────────────────────────────

    function _proposalParams()
        internal
        view
        returns (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        )
    {
        targets = new address[](1);
        targets[0] = address(token);
        values = new uint256[](1);
        values[0] = 0;
        calldatas = new bytes[](1);
        calldatas[0] = abi.encodeWithSelector(SisoulToken.mint.selector, recipient, 1000 ether);
        description = "mint 1000 SIS to recipient";
    }

    function _propose(address proposer) internal returns (uint256 proposalId) {
        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        vm.prank(proposer);
        proposalId = gov.propose(targets, values, calldatas, description);
    }

    // ── 1. constructor params ─────────────────────────────────────────────────

    function test_VotingPeriod() public view {
        assertEq(gov.votingPeriod(), 7 days);
    }

    function test_VotingDelay() public view {
        assertEq(gov.votingDelay(), 1 days);
    }

    function test_ProposalThreshold() public view {
        assertEq(gov.proposalThreshold(), 100 ether);
    }

    function test_QuorumFraction() public view {
        // 4% of supply at timepoint-1
        uint256 totalSupply = token.totalSupply();
        uint256 q = gov.quorum(block.timestamp - 1);
        assertEq(q, (totalSupply * 4) / 100);
    }

    function test_TimelockDelay() public view {
        assertEq(timelock.getMinDelay(), 2 days);
    }

    function test_ClockMode() public view {
        assertEq(gov.CLOCK_MODE(), "mode=timestamp");
        assertEq(uint256(gov.clock()), block.timestamp);
    }

    // ── 2. propose ────────────────────────────────────────────────────────────

    function test_Propose_Success() public {
        uint256 pid = _propose(alice);
        assertGt(pid, 0);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Pending));
    }

    function test_Propose_RevertBelowThreshold() public {
        // Eve has 0 token → 提案应 revert
        address eve = address(0xE7E);
        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        vm.prank(eve);
        vm.expectRevert();
        gov.propose(targets, values, calldatas, description);
    }

    function test_Propose_ThresholdExactlyMet() public {
        // 给 dave 正好 100 SIS — 应可 propose
        address dave = address(0xDA);
        vm.prank(admin);
        token.mint(dave, 100 ether);
        vm.prank(dave);
        token.delegate(dave);
        vm.warp(block.timestamp + 1);
        uint256 pid = _propose(dave);
        assertGt(pid, 0);
    }

    // ── 3. state transitions: Pending → Active ────────────────────────────────

    function test_StatePendingThenActive() public {
        uint256 pid = _propose(alice);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Pending));
        // 跳过 voting delay
        vm.warp(block.timestamp + 1 days + 1);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Active));
    }

    // ── 4. vote ───────────────────────────────────────────────────────────────

    function test_Vote_For_Succeeds() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1); // 1=For
        (uint256 against, uint256 forVotes, uint256 abstain) = gov.proposalVotes(pid);
        assertEq(forVotes, BIG_MINT);
        assertEq(against, 0);
        assertEq(abstain, 0);
    }

    function test_Vote_Against() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(bob);
        gov.castVote(pid, 0); // 0=Against
        (uint256 against, uint256 forVotes,) = gov.proposalVotes(pid);
        assertEq(against, BIG_MINT);
        assertEq(forVotes, 0);
    }

    function test_Vote_Abstain() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 2); // 2=Abstain
        (,, uint256 abstain) = gov.proposalVotes(pid);
        assertEq(abstain, BIG_MINT);
    }

    function test_Vote_BeforeActive_Reverts() public {
        uint256 pid = _propose(alice);
        // still Pending
        vm.prank(alice);
        vm.expectRevert();
        gov.castVote(pid, 1);
    }

    function test_Vote_AfterPeriod_Reverts() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 7 days + 2);
        vm.prank(alice);
        vm.expectRevert();
        gov.castVote(pid, 1);
    }

    function test_Vote_DoubleVote_Reverts() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(alice);
        vm.expectRevert();
        gov.castVote(pid, 1);
    }

    // ── 5. quorum / outcomes ──────────────────────────────────────────────────

    function test_Defeated_QuorumNotMet() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        // 仅 carol 投 (0.06%) — 远不够 4% quorum
        vm.prank(carol);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Defeated));
    }

    function test_Defeated_AgainstWins() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        // alice 投 For (5%, 过 quorum), bob 投 Against (5%) — for==against → Defeated (For 必须 strictly >)
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 0);
        vm.warp(block.timestamp + 7 days + 1);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Defeated));
    }

    function test_Succeeded_QuorumMet_ForWins() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Succeeded));
    }

    // ── 6. queue + execute ────────────────────────────────────────────────────

    function test_Queue_Succeeded() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);

        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        gov.queue(targets, values, calldatas, keccak256(bytes(description)));
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Queued));
    }

    function test_Execute_AfterTimelock() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);

        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        bytes32 descHash = keccak256(bytes(description));
        gov.queue(targets, values, calldatas, descHash);

        // 必须等 2 days timelock
        vm.warp(block.timestamp + 2 days + 1);

        // 转 minter role 给 timelock 才能执行 mint
        vm.prank(admin);
        token.grantRole(token.MINTER_ROLE(), address(timelock));

        uint256 before = token.balanceOf(recipient);
        gov.execute(targets, values, calldatas, descHash);
        assertEq(token.balanceOf(recipient), before + 1000 ether);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Executed));
    }

    function test_Execute_BeforeTimelock_Reverts() public {
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);

        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        bytes32 descHash = keccak256(bytes(description));
        gov.queue(targets, values, calldatas, descHash);

        // 不等 timelock
        vm.expectRevert();
        gov.execute(targets, values, calldatas, descHash);
        // 静默忽略 pid (避免未使用 warning)
        pid;
    }

    // ── 7. cancel ─────────────────────────────────────────────────────────────

    function test_Cancel_ByProposer() public {
        uint256 pid = _propose(alice);
        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        bytes32 descHash = keccak256(bytes(description));
        vm.prank(alice);
        gov.cancel(targets, values, calldatas, descHash);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Canceled));
    }

    function test_Cancel_ByOtherFails() public {
        _propose(alice);
        (
            address[] memory targets,
            uint256[] memory values,
            bytes[] memory calldatas,
            string memory description
        ) = _proposalParams();
        bytes32 descHash = keccak256(bytes(description));
        vm.prank(bob);
        vm.expectRevert();
        gov.cancel(targets, values, calldatas, descHash);
    }

    // ── 8. expire (Succeeded 后不 queue 超时) ─────────────────────────────────

    function test_Expired_NotQueued() public {
        // OZ Governor v5 没 Expired 状态 (queue 在 timelock 内有自己的 grace period);
        // 这里测 Succeeded 状态: 长时间不 queue 仍是 Succeeded.
        uint256 pid = _propose(alice);
        vm.warp(block.timestamp + 1 days + 1);
        vm.prank(alice);
        gov.castVote(pid, 1);
        vm.prank(bob);
        gov.castVote(pid, 1);
        vm.warp(block.timestamp + 7 days + 1);
        // 30 天后仍 Succeeded (Governor 本身无 Expired)
        vm.warp(block.timestamp + 30 days);
        assertEq(uint256(gov.state(pid)), uint256(IGovernor.ProposalState.Succeeded));
    }

    // ── 9. token features ─────────────────────────────────────────────────────

    function test_Token_CapEnforced() public {
        vm.prank(admin);
        vm.expectRevert();
        token.mint(alice, CAP); // 已 mint 部分 + CAP > cap
    }

    function test_Token_OnlyMinterCanMint() public {
        vm.prank(alice);
        vm.expectRevert();
        token.mint(alice, 1 ether);
    }

    function test_Token_DelegateAndVotes() public {
        // 给 dave mint, 不 delegate → getVotes = 0
        address dave = address(0xDA);
        vm.prank(admin);
        token.mint(dave, 1000 ether);
        vm.warp(block.timestamp + 1);
        assertEq(token.getVotes(dave), 0);
        vm.prank(dave);
        token.delegate(dave);
        vm.warp(block.timestamp + 1);
        assertEq(token.getVotes(dave), 1000 ether);
    }
}
