// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SkillRegistry} from "../src/SkillRegistry.sol";

contract SkillRegistryTest is Test {
    SkillRegistry internal reg;
    address internal admin = address(0xA11CE);
    address internal alice = address(0xA1);
    address internal bob = address(0xB0B);

    bytes32 internal aliceDID = keccak256(bytes("did:sisoul:alice"));
    bytes32 internal bobDID = keccak256(bytes("did:sisoul:bob"));

    function setUp() public {
        reg = new SkillRegistry(admin);
    }

    function test_Register_Success() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "code-review", "ar://cid1");
        SkillRegistry.Skill memory s = reg.getSkill(sid);
        assertEq(s.ownerAddr, alice);
        assertEq(s.version, 1);
        assertEq(s.currentCID, "ar://cid1");
        assertFalse(s.frozen);
    }

    function test_Register_RevertEmptyInput() public {
        vm.prank(alice);
        vm.expectRevert(SkillRegistry.InvalidInput.selector);
        reg.registerSkill(bytes32(0), "slug", "cid");

        vm.prank(alice);
        vm.expectRevert(SkillRegistry.InvalidInput.selector);
        reg.registerSkill(aliceDID, "", "cid");

        vm.prank(alice);
        vm.expectRevert(SkillRegistry.InvalidInput.selector);
        reg.registerSkill(aliceDID, "slug", "");
    }

    function test_Register_RevertDuplicate() public {
        vm.startPrank(alice);
        reg.registerSkill(aliceDID, "code-review", "cid1");
        vm.expectRevert();
        reg.registerSkill(aliceDID, "code-review", "cid2");
        vm.stopPrank();
    }

    function test_Update_VersionAndCID() public {
        vm.startPrank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        reg.updateSkill(sid, "cid2");
        reg.updateSkill(sid, "cid3");
        vm.stopPrank();
        SkillRegistry.Skill memory s = reg.getSkill(sid);
        assertEq(s.version, 3);
        assertEq(s.currentCID, "cid3");

        string[] memory hist = reg.historyOf(sid);
        assertEq(hist.length, 3);
        assertEq(hist[0], "cid1");
        assertEq(hist[2], "cid3");
    }

    function test_Update_NotOwner_Reverts() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        vm.prank(bob);
        vm.expectRevert(abi.encodeWithSelector(SkillRegistry.NotSkillOwner.selector, sid));
        reg.updateSkill(sid, "cid2");
    }

    function test_Freeze_DAOOnly() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        vm.prank(bob);
        vm.expectRevert();
        reg.freezeSkill(sid);
        vm.prank(admin);
        reg.freezeSkill(sid);
        assertTrue(reg.getSkill(sid).frozen);
    }

    function test_Update_FrozenReverts() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        vm.prank(admin);
        reg.freezeSkill(sid);
        vm.prank(alice);
        vm.expectRevert(abi.encodeWithSelector(SkillRegistry.SkillFrozenError.selector, sid));
        reg.updateSkill(sid, "cid2");
    }

    function test_Unfreeze() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        vm.startPrank(admin);
        reg.freezeSkill(sid);
        reg.unfreezeSkill(sid);
        vm.stopPrank();
        assertFalse(reg.getSkill(sid).frozen);
        vm.prank(alice);
        reg.updateSkill(sid, "cid2");
        assertEq(reg.getSkill(sid).currentCID, "cid2");
    }

    function test_TransferOwner() public {
        vm.prank(alice);
        bytes32 sid = reg.registerSkill(aliceDID, "cr", "cid1");
        vm.prank(alice);
        reg.transferOwner(sid, bob);
        assertEq(reg.getSkill(sid).ownerAddr, bob);
        vm.prank(bob);
        reg.updateSkill(sid, "cid2");
        assertEq(reg.getSkill(sid).version, 2);
    }

    function test_ComputeSkillId_Stable() public view {
        bytes32 a = reg.computeSkillId(aliceDID, "cr");
        bytes32 b = reg.computeSkillId(aliceDID, "cr");
        assertEq(a, b);
        bytes32 c = reg.computeSkillId(bobDID, "cr");
        assertTrue(a != c);
    }

    function test_SkillsByOwner_Enumerate() public {
        vm.startPrank(alice);
        reg.registerSkill(aliceDID, "a", "1");
        reg.registerSkill(aliceDID, "b", "2");
        vm.stopPrank();
        bytes32[] memory ids = reg.skillsByOwner(alice);
        assertEq(ids.length, 2);
        assertEq(reg.totalSkills(), 2);
    }

    function test_GetSkill_NotFoundReverts() public {
        bytes32 fake = keccak256("nope");
        vm.expectRevert(abi.encodeWithSelector(SkillRegistry.SkillNotFound.selector, fake));
        reg.getSkill(fake);
    }
}
