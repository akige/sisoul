// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";
import {SisoulToken} from "../src/SisoulToken.sol";
import {SisoulGov} from "../src/SisoulGov.sol";
import {PIPRegistry} from "../src/PIPRegistry.sol";
import {SkillRegistry} from "../src/SkillRegistry.sol";

/// @notice 部署 sisoul DAO 完整栈: Token → Timelock (2 days) → Governor → PIPRegistry → SkillRegistry.
///         deployer 是 init admin, 部署完后 renounce admin role 到 Governor / Timelock.
/// @dev forge script script/Deploy.s.sol --rpc-url <RPC> --broadcast --private-key $PK
contract Deploy is Script {
    /// 1B SIS cap (1e9 * 1e18).
    uint256 public constant CAP = 1_000_000_000 ether;
    /// timelock 最小延迟 2 days.
    uint256 public constant TIMELOCK_DELAY = 2 days;

    function run()
        external
        returns (
            SisoulToken token,
            TimelockController timelock,
            SisoulGov gov,
            PIPRegistry pip,
            SkillRegistry skill
        )
    {
        address deployer = msg.sender;

        vm.startBroadcast();

        // 1. Token (deployer = admin + minter, 后续转给 Timelock)
        token = new SisoulToken(deployer, deployer, CAP);

        // 2. Timelock (proposers/executors 之后由 Governor 充任)
        address[] memory proposers = new address[](0);
        address[] memory executors = new address[](0);
        timelock = new TimelockController(TIMELOCK_DELAY, proposers, executors, deployer);

        // 3. Governor
        gov = new SisoulGov(token, timelock);

        // 4. 给 Governor 授权 Timelock 的 PROPOSER_ROLE / EXECUTOR_ROLE
        bytes32 proposerRole = timelock.PROPOSER_ROLE();
        bytes32 executorRole = timelock.EXECUTOR_ROLE();
        bytes32 cancellerRole = timelock.CANCELLER_ROLE();
        timelock.grantRole(proposerRole, address(gov));
        timelock.grantRole(cancellerRole, address(gov));
        timelock.grantRole(executorRole, address(0)); // anyone can execute after delay

        // 5. PIP / Skill registries — admin 转给 Timelock (DAO 控)
        pip = new PIPRegistry(address(timelock));
        skill = new SkillRegistry(address(timelock));

        vm.stopBroadcast();

        console2.log("SisoulToken:", address(token));
        console2.log("TimelockController:", address(timelock));
        console2.log("SisoulGov:", address(gov));
        console2.log("PIPRegistry:", address(pip));
        console2.log("SkillRegistry:", address(skill));
    }
}
