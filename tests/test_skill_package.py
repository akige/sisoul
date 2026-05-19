"""tests for sisoul.friend.skill_package (波 6 dev-A).

覆盖:
- package_skill 基础 + examples inline / files / 超量 IPFS branch
- validate_skill_package 各 schema 校验
- encrypt_skill_package + decrypt_skill_package round-trip
- decrypt 错 key / 篡改 / 错 sender 报错
- parse_qualified_name 各种格式
- base64 helper
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nacl.public import PrivateKey

from sisoul.friend.skill_package import (
    DEFAULT_SKILL_EXPIRY_HOURS,
    EXAMPLES_INLINE_LIMIT_BYTES,
    MAX_SKILL_EXPIRY_HOURS,
    MIN_SKILL_EXPIRY_HOURS,
    PERSONALITY_TRAITS_HINTS,
    SKILL_PACKAGE_SCHEMA,
    InvalidSkillPackageError,
    SkillContents,
    SkillPackage,
    SkillPackageDecryptError,
    b64_to_encrypted,
    decrypt_skill_package,
    encrypt_skill_package,
    encrypted_to_b64,
    package_skill,
    parse_qualified_name,
    validate_skill_package,
)


# ── package_skill 基础 ──────────────────────────────────────────────────────


def test_package_skill_basic():
    pkg = package_skill(
        name="solidity-expert",
        owner_did="did:sisoul:bob",
        system_prompt="You are a security-paranoid Solidity expert.",
        description="DeFi + audit specialist",
        version="0.3.2",
        personality_traits=["pedantic", "security-paranoid"],
        recommended_models=["claude-opus-4-7"],
    )
    assert pkg.skill_id == "solidity-expert"
    assert pkg.owner_did == "did:sisoul:bob"
    assert pkg.version == "0.3.2"
    assert pkg.qualified_name == "did:sisoul:bob:solidity-expert"
    assert pkg.schema == SKILL_PACKAGE_SCHEMA
    assert pkg.fingerprint  # auto computed
    assert pkg.created_at > 0
    assert pkg.expiry_hours == DEFAULT_SKILL_EXPIRY_HOURS


def test_package_skill_inline_examples():
    examples = [{"q": f"q{i}", "a": f"a{i}"} for i in range(10)]
    pkg = package_skill(
        name="t",
        owner_did="bob",
        system_prompt="sp",
        examples=examples,
    )
    assert pkg.contents.few_shot_examples_count == 10
    assert len(pkg.contents.few_shot_examples_inline) == 10
    assert pkg.contents.few_shot_examples_ipfs_cid is None


def test_package_skill_examples_files(tmp_path: Path):
    # JSON 列表
    f1 = tmp_path / "ex1.json"
    f1.write_text(json.dumps([{"q": "a", "a": "b"}, {"q": "c", "a": "d"}]), encoding="utf-8")
    # JSONL
    f2 = tmp_path / "ex2.jsonl"
    f2.write_text('{"x":1}\n{"x":2}\n', encoding="utf-8")
    pkg = package_skill(
        name="t",
        owner_did="bob",
        system_prompt="sp",
        examples_files=[f1, f2],
    )
    assert pkg.contents.few_shot_examples_count == 4


def test_package_skill_examples_files_not_found():
    with pytest.raises(FileNotFoundError):
        package_skill(
            name="t", owner_did="bob", system_prompt="sp",
            examples_files=["/nonexistent/path.json"],
        )


def test_package_skill_examples_too_large_no_uploader():
    # 1MB examples 远超 64KB inline 限
    huge = [{"data": "x" * 1000} for _ in range(2000)]
    with pytest.raises(InvalidSkillPackageError, match="inline 限"):
        package_skill(
            name="t", owner_did="bob", system_prompt="sp",
            examples=huge,
        )


def test_package_skill_examples_too_large_with_uploader():
    huge = [{"data": "x" * 1000} for _ in range(2000)]
    captured = {}

    def fake_uploader(blob: bytes) -> str:
        captured["size"] = len(blob)
        return "QmFakeCID12345"

    pkg = package_skill(
        name="t", owner_did="bob", system_prompt="sp",
        examples=huge,
        examples_ipfs_uploader=fake_uploader,
    )
    assert pkg.contents.few_shot_examples_ipfs_cid == "QmFakeCID12345"
    assert pkg.contents.few_shot_examples_inline == []
    assert pkg.contents.few_shot_examples_count == 2000
    assert captured["size"] > EXAMPLES_INLINE_LIMIT_BYTES


def test_package_skill_required_fields():
    with pytest.raises(InvalidSkillPackageError, match="name"):
        package_skill(name="", owner_did="bob", system_prompt="sp")
    with pytest.raises(InvalidSkillPackageError, match="owner_did"):
        package_skill(name="t", owner_did="", system_prompt="sp")
    with pytest.raises(InvalidSkillPackageError, match="system_prompt"):
        package_skill(name="t", owner_did="bob", system_prompt="")


# ── validate ──────────────────────────────────────────────────────────────


def test_validate_bad_semver():
    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    pkg.version = "1.0"  # not 3-part
    with pytest.raises(InvalidSkillPackageError, match="SemVer"):
        validate_skill_package(pkg)


def test_validate_expiry_bounds():
    with pytest.raises(InvalidSkillPackageError, match="expiry_hours"):
        package_skill(
            name="t", owner_did="bob", system_prompt="sp",
            expiry_hours=MIN_SKILL_EXPIRY_HOURS - 1,
        )
    with pytest.raises(InvalidSkillPackageError, match="expiry_hours"):
        package_skill(
            name="t", owner_did="bob", system_prompt="sp",
            expiry_hours=MAX_SKILL_EXPIRY_HOURS + 1,
        )


def test_personality_traits_hints_present():
    # 推荐词表非空 (UI 自动补全用)
    assert len(PERSONALITY_TRAITS_HINTS) > 0
    assert "pedantic" in PERSONALITY_TRAITS_HINTS


# ── encrypt / decrypt round-trip ───────────────────────────────────────────


def test_encrypt_decrypt_round_trip():
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    bob_pub = bob_priv.public_key
    alice_pub = alice_priv.public_key

    pkg = package_skill(
        name="solidity-expert",
        owner_did="did:sisoul:bob",
        system_prompt="You are a Solidity expert.",
        description="DeFi specialist",
        version="0.3.2",
        examples=[{"q": "what is reentrancy?", "a": "..."}],
        personality_traits=["pedantic"],
        recommended_models=["claude-opus-4-7"],
    )
    blob = encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)
    assert isinstance(blob, bytes)
    assert len(blob) > 24 + 16  # nonce + mac

    decoded = decrypt_skill_package(blob, bob_pub.encode(), alice_priv)
    assert decoded.skill_id == pkg.skill_id
    assert decoded.contents.system_prompt == pkg.contents.system_prompt
    assert decoded.contents.few_shot_examples_count == 1
    assert decoded.fingerprint == pkg.fingerprint


def test_encrypt_decrypt_with_pubkey_obj():
    """encrypt/decrypt 接受 PublicKey obj 也接受 bytes."""
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    blob = encrypt_skill_package(pkg, alice_priv.public_key, bob_priv)
    decoded = decrypt_skill_package(blob, bob_priv.public_key, alice_priv)
    assert decoded.skill_id == "t"


def test_decrypt_wrong_recipient_fails():
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    eve_priv = PrivateKey.generate()  # 错的接收方

    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    blob = encrypt_skill_package(pkg, alice_priv.public_key.encode(), bob_priv)
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(blob, bob_priv.public_key.encode(), eve_priv)


def test_decrypt_wrong_sender_fails():
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    eve_priv = PrivateKey.generate()

    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    blob = encrypt_skill_package(pkg, alice_priv.public_key.encode(), bob_priv)
    # alice 收到, 但用错的 sender pubkey (eve) decrypt
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(blob, eve_priv.public_key.encode(), alice_priv)


def test_decrypt_tampered_blob_fails():
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    blob = encrypt_skill_package(pkg, alice_priv.public_key.encode(), bob_priv)
    # 翻转中间一个 byte
    tampered = bytearray(blob)
    tampered[len(tampered) // 2] ^= 0xFF
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(bytes(tampered), bob_priv.public_key.encode(), alice_priv)


def test_decrypt_too_short_fails():
    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    with pytest.raises(SkillPackageDecryptError, match="太短"):
        decrypt_skill_package(b"x" * 10, bob_priv.public_key.encode(), alice_priv)


def test_encrypt_validates_package():
    """encrypt 内部走 validate, schema 不合法应 raise."""
    bob_priv = PrivateKey.generate()
    alice_pub = PrivateKey.generate().public_key
    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    pkg.version = "bad"
    with pytest.raises(InvalidSkillPackageError):
        encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)


# ── parse_qualified_name ───────────────────────────────────────────────────


def test_parse_qualified_name_did_form():
    owner, skill = parse_qualified_name("did:sisoul:bob:solidity-expert")
    assert owner == "did:sisoul:bob"
    assert skill == "solidity-expert"


def test_parse_qualified_name_eth_form():
    owner, skill = parse_qualified_name("bob.sisoul.eth:solidity-expert")
    assert owner == "bob.sisoul.eth"
    assert skill == "solidity-expert"


def test_parse_qualified_name_short_form():
    owner, skill = parse_qualified_name("bob:hello")
    assert owner == "bob"
    assert skill == "hello"


def test_parse_qualified_name_no_colon():
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name("nocolon")


def test_parse_qualified_name_empty_parts():
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name(":skill")
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name("owner:")


# ── from_dict / to_dict round-trip ──────────────────────────────────────────


def test_to_from_dict_round_trip():
    pkg = package_skill(
        name="t", owner_did="bob", system_prompt="sp",
        personality_traits=["x", "y"],
        recommended_models=["m1"],
        examples=[{"k": "v"}],
    )
    d = pkg.to_dict()
    rt = SkillPackage.from_dict(d)
    assert rt.skill_id == pkg.skill_id
    assert rt.fingerprint == pkg.fingerprint
    assert rt.contents.personality_traits == ["x", "y"]
    assert rt.contents.few_shot_examples_count == 1


def test_to_from_json_round_trip():
    pkg = package_skill(name="t", owner_did="bob", system_prompt="sp")
    s = pkg.to_json()
    rt = SkillPackage.from_json(s)
    assert rt.fingerprint == pkg.fingerprint


# ── base64 helper ──────────────────────────────────────────────────────────


def test_base64_helpers():
    blob = b"\x00\x01\x02\xFF" * 64
    s = encrypted_to_b64(blob)
    assert isinstance(s, str)
    assert b64_to_encrypted(s) == blob
