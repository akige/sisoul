// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {PaymentChannel} from "../src/PaymentChannel.sol";

/// @dev 6-decimals mock USDC.
contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {}

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract PaymentChannelTest is Test {
    PaymentChannel internal channel;
    MockUSDC internal usdc;

    uint256 internal constant PAYER_PK = 0xA11CE;
    address internal payer;
    address internal payee = makeAddr("payee");
    address internal feeAddr = makeAddr("feeMultisig");

    uint256 internal constant DEPOSIT = 1_000e6; // 1000 USDC
    uint64 internal expiry;

    function setUp() public {
        payer = vm.addr(PAYER_PK);
        channel = new PaymentChannel(feeAddr, 300);
        usdc = new MockUSDC();
        usdc.mint(payer, DEPOSIT);
        expiry = uint64(block.timestamp + 7 days);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    function _open() internal returns (bytes32 channelId) {
        vm.startPrank(payer);
        usdc.approve(address(channel), DEPOSIT);
        channelId = channel.open(payee, address(usdc), DEPOSIT, expiry);
        vm.stopPrank();
    }

    function _signReceipt(uint256 pk, bytes32 channelId, uint256 cumulativeAmount)
        internal
        view
        returns (bytes memory)
    {
        bytes32 digest = channel.receiptDigest(channelId, cumulativeAmount);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    // ── open ─────────────────────────────────────────────────────────────────

    function test_open_locksDeposit() public {
        assertEq(usdc.balanceOf(payer), DEPOSIT);
        bytes32 channelId = _open();

        // 余额真减: payer 1000e6 -> 0, 合约 0 -> 1000e6
        assertEq(usdc.balanceOf(payer), 0);
        assertEq(usdc.balanceOf(address(channel)), DEPOSIT);

        PaymentChannel.Channel memory ch = channel.getChannel(channelId);
        assertEq(ch.payer, payer);
        assertEq(ch.payee, payee);
        assertEq(ch.deposit, DEPOSIT);
        assertEq(uint8(ch.status), uint8(PaymentChannel.ChannelStatus.Open));
    }

    function test_open_nonceMakesUniqueIds() public {
        usdc.mint(payer, DEPOSIT); // 第二个通道的钱
        vm.startPrank(payer);
        usdc.approve(address(channel), 2 * DEPOSIT);
        bytes32 id1 = channel.open(payee, address(usdc), DEPOSIT, expiry);
        bytes32 id2 = channel.open(payee, address(usdc), DEPOSIT, expiry);
        vm.stopPrank();
        assertTrue(id1 != id2, "same (payer,payee) channels got identical ids");
    }

    // ── close: 核心三方分账 ────────────────────────────────────────────────────

    function test_close_splits_97_3_refund() public {
        bytes32 channelId = _open();

        // payer 累计签 600 USDC 收据
        uint256 cumulative = 600e6;
        bytes memory sig = _signReceipt(PAYER_PK, channelId, cumulative);

        vm.expectEmit(true, false, false, true);
        emit PaymentChannel.ChannelClosed(channelId, cumulative, 582e6, 18e6, 400e6);
        vm.prank(payee);
        channel.close(channelId, cumulative, sig);

        // 三方分账: payee 600 * 97% = 582, fee 600 * 3% = 18, payer 退 1000 - 600 = 400
        assertEq(usdc.balanceOf(payee), 582e6, "payee != 582 USDC (97%)");
        assertEq(usdc.balanceOf(feeAddr), 18e6, "fee multisig != 18 USDC (3%)");
        assertEq(usdc.balanceOf(payer), 400e6, "payer refund != 400 USDC");
        assertEq(usdc.balanceOf(address(channel)), 0, "channel balance != 0 after close");
    }

    function test_close_revert_overpay() public {
        bytes32 channelId = _open();
        uint256 cumulative = DEPOSIT + 1; // 超付
        bytes memory sig = _signReceipt(PAYER_PK, channelId, cumulative);

        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.Overpay.selector, cumulative, DEPOSIT));
        channel.close(channelId, cumulative, sig);
    }

    function test_close_revert_doubleClose() public {
        bytes32 channelId = _open();
        bytes memory sig = _signReceipt(PAYER_PK, channelId, 100e6);

        vm.prank(payee);
        channel.close(channelId, 100e6, sig);

        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.ChannelNotOpen.selector, channelId));
        channel.close(channelId, 100e6, sig);
    }

    function test_close_revert_forgedSignature() public {
        bytes32 channelId = _open();
        uint256 attackerPk = 0xBAD;
        bytes memory forged = _signReceipt(attackerPk, channelId, 600e6);

        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.InvalidSignature.selector, channelId));
        channel.close(channelId, 600e6, forged);
    }

    function test_close_revert_notPayee() public {
        bytes32 channelId = _open();
        bytes memory sig = _signReceipt(PAYER_PK, channelId, 100e6);

        address rando = makeAddr("rando");
        vm.prank(rando);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.NotPayee.selector, channelId));
        channel.close(channelId, 100e6, sig);
    }

    function test_close_revert_receiptFromOtherChannelReplay() public {
        // 同 payer/payee 开两条通道, 通道 A 的收据不能在通道 B 用 (channelId 在签名域里)
        usdc.mint(payer, DEPOSIT);
        vm.startPrank(payer);
        usdc.approve(address(channel), 2 * DEPOSIT);
        bytes32 idA = channel.open(payee, address(usdc), DEPOSIT, expiry);
        bytes32 idB = channel.open(payee, address(usdc), DEPOSIT, expiry);
        vm.stopPrank();

        bytes memory sigForA = _signReceipt(PAYER_PK, idA, 500e6);
        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.InvalidSignature.selector, idB));
        channel.close(idB, 500e6, sigForA);
    }

    // ── cancel ───────────────────────────────────────────────────────────────

    function test_cancel_afterExpiry_fullRefund() public {
        bytes32 channelId = _open();
        assertEq(usdc.balanceOf(payer), 0);

        vm.warp(expiry); // 到达 expiry
        vm.prank(payer);
        channel.cancel(channelId);

        assertEq(usdc.balanceOf(payer), DEPOSIT, "payer full refund != 1000 USDC");
        assertEq(usdc.balanceOf(address(channel)), 0);
    }

    function test_cancel_revert_beforeExpiry() public {
        bytes32 channelId = _open();
        vm.prank(payer);
        vm.expectRevert(
            abi.encodeWithSelector(PaymentChannel.NotYetExpired.selector, channelId, expiry)
        );
        channel.cancel(channelId);
    }

    function test_cancel_revert_notPayer() public {
        bytes32 channelId = _open();
        vm.warp(expiry);
        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.NotPayer.selector, channelId));
        channel.cancel(channelId);
    }

    function test_close_revert_afterCancel() public {
        bytes32 channelId = _open();
        vm.warp(expiry);
        vm.prank(payer);
        channel.cancel(channelId);

        bytes memory sig = _signReceipt(PAYER_PK, channelId, 100e6);
        vm.prank(payee);
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.ChannelNotOpen.selector, channelId));
        channel.close(channelId, 100e6, sig);
    }

    // ── fee 配置 ─────────────────────────────────────────────────────────────

    function test_constructor_revert_feeBpsAbove500() public {
        vm.expectRevert(abi.encodeWithSelector(PaymentChannel.FeeBpsTooHigh.selector, 600, 500));
        new PaymentChannel(feeAddr, 600);
    }

    function test_constructor_allows500() public {
        PaymentChannel pc = new PaymentChannel(feeAddr, 500);
        assertEq(pc.feeBps(), 500);
    }

    function test_setFeeAddress_onlyOwner() public {
        address newFee = makeAddr("newFeeMultisig");
        channel.setFeeAddress(newFee);
        assertEq(channel.feeAddress(), newFee);

        vm.prank(payee);
        vm.expectRevert();
        channel.setFeeAddress(payee);
    }

    // ── fuzz: 三方分账守恒 ─────────────────────────────────────────────────────

    function testFuzz_close_conservation(uint256 cumulativeAmount) public {
        cumulativeAmount = bound(cumulativeAmount, 0, DEPOSIT);
        bytes32 channelId = _open();
        bytes memory sig = _signReceipt(PAYER_PK, channelId, cumulativeAmount);

        vm.prank(payee);
        channel.close(channelId, cumulativeAmount, sig);

        uint256 toPayee = usdc.balanceOf(payee);
        uint256 toFee = usdc.balanceOf(feeAddr);
        uint256 refund = usdc.balanceOf(payer);

        // 守恒: 三方拿到的钱 == deposit, 合约清零
        assertEq(toPayee + toFee + refund, DEPOSIT, "conservation violated");
        assertEq(usdc.balanceOf(address(channel)), 0, "channel balance != 0 after close");
        // fee 精确等于 floor(3%)
        assertEq(toFee, (cumulativeAmount * 300) / 10_000, "fee != floor(3%)");
        assertEq(refund, DEPOSIT - cumulativeAmount, "refund != deposit - cumulative");
    }
}
