// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {PaymentChannel} from "../src/PaymentChannel.sol";

/// @title DeployPaymentChannel — OP Sepolia 部署脚本.
/// @notice 用法 (不在本任务真跑):
///   FEE_ADDRESS=0x... PRIVATE_KEY=0x... \
///   forge script script/DeployPaymentChannel.s.sol \
///     --rpc-url optimism_sepolia --broadcast --verify
contract DeployPaymentChannel is Script {
    /// @dev protocol fee = 3% (300 bps), M4 定值.
    uint256 internal constant FEE_BPS = 300;

    function run() external returns (PaymentChannel channel) {
        address feeAddress = vm.envAddress("FEE_ADDRESS");
        uint256 deployerPk = vm.envUint("PRIVATE_KEY");

        vm.startBroadcast(deployerPk);
        channel = new PaymentChannel(feeAddress, FEE_BPS);
        vm.stopBroadcast();

        console2.log("PaymentChannel deployed at:", address(channel));
        console2.log("  feeAddress:", feeAddress);
        console2.log("  feeBps:", FEE_BPS);
        console2.log("  owner:", vm.addr(deployerPk));
    }
}
