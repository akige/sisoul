// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title PaymentChannel — 单向双签状态通道 (payer → payee), 流式 micropay 借 LLM 用.
/// @notice M4: 借入方 (payer) 锁 ERC20 (USDC) 开通道; 借出方 (payee) 流式回 token,
///         payer 链下签递增 cumulativeAmount 的 EIP-712 收据; payee 任意时刻拿最后一张
///         收据上链 close 结算. 结算时抽 feeBps (3%) protocol fee 给 feeAddress,
///         剩余 deposit 退还 payer. expiry 后 payer 可单方 cancel 全额取回.
contract PaymentChannel is EIP712, ReentrancyGuard, Ownable {
    using SafeERC20 for IERC20;

    // ── 常量 ─────────────────────────────────────────────────────────────────

    /// @dev EIP-712 收据 typehash: payer 对 (channelId, cumulativeAmount) 签名.
    bytes32 public constant RECEIPT_TYPEHASH =
        keccak256("Receipt(bytes32 channelId,uint256 cumulativeAmount)");

    /// @dev protocol fee 硬上限 5% (500 bps). 构造与后续调整都不可超过.
    uint256 public constant MAX_FEE_BPS = 500;

    uint256 private constant BPS_DENOMINATOR = 10_000;

    // ── 状态 ─────────────────────────────────────────────────────────────────

    enum ChannelStatus {
        None, // 不存在
        Open, // 已开
        Closed, // 已由 payee 凭收据结算
        Cancelled // 已由 payer expiry 后取回
    }

    struct Channel {
        address payer;
        address payee;
        address token;
        uint256 deposit;
        uint64 expiry; // unix 时间戳, 之后 payer 可单方 cancel
        ChannelStatus status;
    }

    /// channelId => Channel
    mapping(bytes32 => Channel) private _channels;

    /// payer => 自增 nonce (防 channelId 碰撞 / 重放旧通道收据)
    mapping(address => uint256) public nonces;

    /// protocol fee 接收地址 (开发团队多签)
    address public feeAddress;

    /// protocol fee, 单位 bps (300 = 3%)
    uint256 public immutable feeBps;

    // ── 事件 ─────────────────────────────────────────────────────────────────

    event ChannelOpened(
        bytes32 indexed channelId,
        address indexed payer,
        address indexed payee,
        address token,
        uint256 deposit,
        uint64 expiry
    );
    event ChannelClosed(
        bytes32 indexed channelId,
        uint256 cumulativeAmount,
        uint256 paidToPayee,
        uint256 feeAmount,
        uint256 refundToPayer
    );
    event ChannelCancelled(bytes32 indexed channelId, uint256 refundToPayer);
    event FeeAddressUpdated(address indexed oldFeeAddress, address indexed newFeeAddress);

    // ── 错误 ─────────────────────────────────────────────────────────────────

    error FeeBpsTooHigh(uint256 feeBps, uint256 maxFeeBps);
    error ZeroAddress();
    error ZeroDeposit();
    error ExpiryInPast(uint64 expiry);
    error ChannelNotOpen(bytes32 channelId);
    error NotPayee(bytes32 channelId);
    error NotPayer(bytes32 channelId);
    error Overpay(uint256 cumulativeAmount, uint256 deposit);
    error InvalidSignature(bytes32 channelId);
    error NotYetExpired(bytes32 channelId, uint64 expiry);

    // ── 构造 ─────────────────────────────────────────────────────────────────

    constructor(address feeAddress_, uint256 feeBps_)
        EIP712("SisoulPaymentChannel", "1")
        Ownable(msg.sender)
    {
        if (feeAddress_ == address(0)) revert ZeroAddress();
        if (feeBps_ > MAX_FEE_BPS) revert FeeBpsTooHigh(feeBps_, MAX_FEE_BPS);
        feeAddress = feeAddress_;
        feeBps = feeBps_;
    }

    // ── 核心流程 ──────────────────────────────────────────────────────────────

    /// @notice payer 开通道并锁入 ERC20 deposit. 需先 approve 本合约.
    /// @return channelId keccak256(payer, payee, nonce) — per-payer nonce 自增防撞.
    function open(address payee, address token, uint256 deposit, uint64 expiry)
        external
        nonReentrant
        returns (bytes32 channelId)
    {
        if (payee == address(0) || token == address(0)) revert ZeroAddress();
        if (deposit == 0) revert ZeroDeposit();
        if (expiry <= block.timestamp) revert ExpiryInPast(expiry);

        uint256 nonce = nonces[msg.sender]++;
        channelId = keccak256(abi.encodePacked(msg.sender, payee, nonce));
        // nonce 自增保证 id 唯一, 但仍防御性断言
        assert(_channels[channelId].status == ChannelStatus.None);

        _channels[channelId] = Channel({
            payer: msg.sender,
            payee: payee,
            token: token,
            deposit: deposit,
            expiry: expiry,
            status: ChannelStatus.Open
        });

        IERC20(token).safeTransferFrom(msg.sender, address(this), deposit);

        emit ChannelOpened(channelId, msg.sender, payee, token, deposit, expiry);
    }

    /// @notice payee 凭 payer 的 EIP-712 收据结算通道. 只能 close 一次.
    /// @param cumulativeAmount payer 累计应付额 (单调递增, payee 自然只会提交最大一张)
    /// @param payerSig payer 对 Receipt(channelId, cumulativeAmount) 的 EIP-712 签名
    function close(bytes32 channelId, uint256 cumulativeAmount, bytes calldata payerSig)
        external
        nonReentrant
    {
        Channel storage ch = _channels[channelId];
        if (ch.status != ChannelStatus.Open) revert ChannelNotOpen(channelId);
        if (msg.sender != ch.payee) revert NotPayee(channelId);
        if (cumulativeAmount > ch.deposit) revert Overpay(cumulativeAmount, ch.deposit);

        bytes32 digest = receiptDigest(channelId, cumulativeAmount);
        address signer = ECDSA.recover(digest, payerSig);
        if (signer != ch.payer) revert InvalidSignature(channelId);

        ch.status = ChannelStatus.Closed;

        uint256 feeAmount = (cumulativeAmount * feeBps) / BPS_DENOMINATOR;
        uint256 paidToPayee = cumulativeAmount - feeAmount;
        uint256 refundToPayer = ch.deposit - cumulativeAmount;

        IERC20 token = IERC20(ch.token);
        if (paidToPayee > 0) token.safeTransfer(ch.payee, paidToPayee);
        if (feeAmount > 0) token.safeTransfer(feeAddress, feeAmount);
        if (refundToPayer > 0) token.safeTransfer(ch.payer, refundToPayer);

        emit ChannelClosed(channelId, cumulativeAmount, paidToPayee, feeAmount, refundToPayer);
    }

    /// @notice expiry 后 payer 单方取回全部 deposit (payee 失联兜底).
    function cancel(bytes32 channelId) external nonReentrant {
        Channel storage ch = _channels[channelId];
        if (ch.status != ChannelStatus.Open) revert ChannelNotOpen(channelId);
        if (msg.sender != ch.payer) revert NotPayer(channelId);
        if (block.timestamp < ch.expiry) revert NotYetExpired(channelId, ch.expiry);

        ch.status = ChannelStatus.Cancelled;

        IERC20(ch.token).safeTransfer(ch.payer, ch.deposit);

        emit ChannelCancelled(channelId, ch.deposit);
    }

    // ── 管理 ─────────────────────────────────────────────────────────────────

    /// @notice owner 更换 fee 接收地址 (e.g. 多签轮换). feeBps 不可改 (immutable ≤ 500).
    function setFeeAddress(address newFeeAddress) external onlyOwner {
        if (newFeeAddress == address(0)) revert ZeroAddress();
        emit FeeAddressUpdated(feeAddress, newFeeAddress);
        feeAddress = newFeeAddress;
    }

    // ── 视图 ─────────────────────────────────────────────────────────────────

    function getChannel(bytes32 channelId) external view returns (Channel memory) {
        return _channels[channelId];
    }

    /// @notice 收据的 EIP-712 digest, 供链下签名方/SDK 对齐.
    function receiptDigest(bytes32 channelId, uint256 cumulativeAmount) public view returns (bytes32) {
        return _hashTypedDataV4(keccak256(abi.encode(RECEIPT_TYPEHASH, channelId, cumulativeAmount)));
    }

    /// @notice EIP-712 domain separator (链下 SDK 用).
    function domainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}
