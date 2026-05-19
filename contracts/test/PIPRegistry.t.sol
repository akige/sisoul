// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {PIPRegistry} from "../src/PIPRegistry.sol";

contract PIPRegistryTest is Test {
    PIPRegistry internal pip;
    address internal admin = address(0xA11CE);
    address internal alice = address(0xA1);
    address internal bob = address(0xB0B);

    function setUp() public {
        pip = new PIPRegistry(admin);
    }

    function test_Register_Success() public {
        vm.prank(alice);
        pip.registerPIP(1, "PIP-001 DID binding", "ipfs://cid001");
        PIPRegistry.PIP memory p = pip.getPIP(1);
        assertEq(p.id, 1);
        assertEq(p.author, alice);
        assertEq(uint256(p.status), uint256(PIPRegistry.Status.Draft));
        assertEq(p.specCID, "ipfs://cid001");
    }

    function test_Register_RevertZeroId() public {
        vm.expectRevert(PIPRegistry.PIPInvalidId.selector);
        pip.registerPIP(0, "bad", "cid");
    }

    function test_Register_RevertDuplicate() public {
        vm.prank(alice);
        pip.registerPIP(1, "PIP-001", "cid1");
        vm.expectRevert(abi.encodeWithSelector(PIPRegistry.PIPAlreadyRegistered.selector, uint256(1)));
        pip.registerPIP(1, "again", "cid2");
    }

    function test_GetPIP_RevertNotFound() public {
        vm.expectRevert(abi.encodeWithSelector(PIPRegistry.PIPNotFound.selector, uint256(99)));
        pip.getPIP(99);
    }

    function test_SetStatus_DraftToReview() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.prank(admin);
        pip.setStatus(1, PIPRegistry.Status.Review);
        assertEq(uint256(pip.getStatus(1)), uint256(PIPRegistry.Status.Review));
    }

    function test_SetStatus_FullPath_ToFinal() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.startPrank(admin);
        pip.setStatus(1, PIPRegistry.Status.Review);
        pip.setStatus(1, PIPRegistry.Status.FinalCall);
        pip.setStatus(1, PIPRegistry.Status.Final);
        vm.stopPrank();
        assertEq(uint256(pip.getStatus(1)), uint256(PIPRegistry.Status.Final));
    }

    function test_SetStatus_InvalidTransition() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.prank(admin);
        // Draft → Final 直接跳, 非法
        vm.expectRevert(
            abi.encodeWithSelector(
                PIPRegistry.PIPInvalidTransition.selector,
                PIPRegistry.Status.Draft,
                PIPRegistry.Status.Final
            )
        );
        pip.setStatus(1, PIPRegistry.Status.Final);
    }

    function test_SetStatus_OnlyAdmin() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.prank(bob);
        vm.expectRevert();
        pip.setStatus(1, PIPRegistry.Status.Review);
    }

    function test_UpdateSpecCID_AuthorInDraft() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "cid1");
        vm.prank(alice);
        pip.updateSpecCID(1, "cid2");
        assertEq(pip.getPIP(1).specCID, "cid2");
    }

    function test_UpdateSpecCID_NotAuthor_Reverts() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.prank(bob);
        vm.expectRevert();
        pip.updateSpecCID(1, "cid2");
    }

    function test_UpdateSpecCID_FrozenAfterFinalCall() public {
        vm.prank(alice);
        pip.registerPIP(1, "t", "c");
        vm.startPrank(admin);
        pip.setStatus(1, PIPRegistry.Status.Review);
        pip.setStatus(1, PIPRegistry.Status.FinalCall);
        vm.stopPrank();
        vm.prank(alice);
        vm.expectRevert();
        pip.updateSpecCID(1, "cid2");
    }

    function test_Supersede() public {
        vm.startPrank(alice);
        pip.registerPIP(1, "old", "c1");
        pip.registerPIP(2, "new", "c2");
        vm.stopPrank();
        vm.prank(admin);
        pip.supersede(1, 2);
        PIPRegistry.PIP memory p = pip.getPIP(1);
        assertEq(p.supersededBy, 2);
        assertEq(uint256(p.status), uint256(PIPRegistry.Status.Superseded));
    }

    function test_TotalAndEnumerate() public {
        vm.startPrank(alice);
        pip.registerPIP(1, "a", "c1");
        pip.registerPIP(2, "b", "c2");
        pip.registerPIP(4, "d", "c4");
        vm.stopPrank();
        assertEq(pip.totalPIPs(), 3);
        assertEq(pip.pipIdAt(0), 1);
        assertEq(pip.pipIdAt(2), 4);
        uint256[] memory ids = pip.allIds();
        assertEq(ids.length, 3);
    }
}
