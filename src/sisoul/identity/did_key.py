"""sisoul · did:key 轻量化 (Wave B' P0-3 · agent-B2).

§36 Wave B' §3 P0-3. https://w3c-ccg.github.io/did-method-key/

设计要点 (跟现 `did.py` 的 `did:sisoul:` mock 共存):

- `did:key:` 标准 W3C-CCG 草案; identifier = multibase(base58btc, multicodec_prefix || raw_pubkey).
- multicodec prefix:
  * 0xed 0x01 → Ed25519 public key (32B)
  * 0xec 0x01 → X25519 public key (32B, libsodium box 用)
- sisoul 选 **X25519** (`0xec 0x01`) 因为:
  * `friend/encrypted_proxy.py` 用 libsodium Box (curve25519/X25519) 派生 session key
  * Ed25519 ↔ X25519 可互转 (但有歧义 / 实现易错), 直接派 X25519 最简
  * 签名场景 sisoul 还没有 (W3C VC 是 v1.1-public 才需要), 故不需要 Ed25519
- 派生路径:
  * BIP-39 mnemonic → master_seed (PBKDF2 64B) → derive_subkey(master, "did:key", 0) (32B)
  * 32B 作 X25519 PrivateKey seed → PublicKey 32B → multicodec wrap → base58btc → did:key:z...
- 跨设备一致性: 同 BIP-39 + 同 passphrase + 同 index → 同 did:key. 无需链上注册, 0 gas.

不引外部依赖 (base58btc / multicodec 自实现).
"""

from __future__ import annotations

from dataclasses import dataclass

from nacl.public import PrivateKey, PublicKey

from sisoul.identity.seed import derive_subkey


X25519_PUB_MULTICODEC_PREFIX = b"\xec\x01"
ED25519_PUB_MULTICODEC_PREFIX = b"\xed\x01"
DID_KEY_SCHEME = "did:key:"
MULTIBASE_BASE58BTC_PREFIX = "z"
X25519_PUBKEY_SIZE = 32
X25519_PRIVKEY_SEED_SIZE = 32
_DID_KEY_PURPOSE = "did:key"


class DidKeyError(Exception):
    """did:key root error."""


class InvalidDidKeyFormatError(DidKeyError):
    """did:key 字符串格式不对."""


class UnsupportedMulticodecError(DidKeyError):
    """did:key multicodec 不支持."""


# base58btc (Bitcoin alphabet, no checksum)
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_ALPHABET_MAP = {c: i for i, c in enumerate(_B58_ALPHABET)}


def base58btc_encode(data: bytes) -> str:
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("data 必须 bytes")
    if len(data) == 0:
        return ""
    n_zeros = 0
    for b in data:
        if b == 0:
            n_zeros += 1
        else:
            break
    num = int.from_bytes(data, "big")
    encoded = ""
    while num > 0:
        num, rem = divmod(num, 58)
        encoded = _B58_ALPHABET[rem] + encoded
    return "1" * n_zeros + encoded


def base58btc_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise ValueError("s 必须 str")
    if len(s) == 0:
        return b""
    n_zeros = 0
    for c in s:
        if c == "1":
            n_zeros += 1
        else:
            break
    num = 0
    for c in s:
        if c not in _B58_ALPHABET_MAP:
            raise InvalidDidKeyFormatError(f"base58btc 含非法字符: {c!r}")
        num = num * 58 + _B58_ALPHABET_MAP[c]
    if num == 0:
        decoded_body = b""
    else:
        nbytes = (num.bit_length() + 7) // 8
        decoded_body = num.to_bytes(nbytes, "big")
    return b"\x00" * n_zeros + decoded_body


@dataclass(frozen=True)
class DidKey:
    did: str
    pubkey: bytes
    multicodec_prefix: bytes

    @property
    def key_type(self) -> str:
        if self.multicodec_prefix == X25519_PUB_MULTICODEC_PREFIX:
            return "X25519"
        if self.multicodec_prefix == ED25519_PUB_MULTICODEC_PREFIX:
            return "Ed25519"
        return f"unknown({self.multicodec_prefix.hex()})"

    @property
    def identifier(self) -> str:
        return self.did[len(DID_KEY_SCHEME):]


def encode_did_key(pubkey: bytes, *, key_type: str = "X25519") -> str:
    if not isinstance(pubkey, (bytes, bytearray)):
        raise ValueError(f"pubkey 必须 bytes, 实际 {type(pubkey).__name__}")
    if len(pubkey) != X25519_PUBKEY_SIZE:
        raise ValueError(f"pubkey 必须 {X25519_PUBKEY_SIZE}B, 实际 {len(pubkey)}B")
    if key_type == "X25519":
        prefix = X25519_PUB_MULTICODEC_PREFIX
    elif key_type == "Ed25519":
        prefix = ED25519_PUB_MULTICODEC_PREFIX
    else:
        raise ValueError(f"未知 key_type: {key_type!r}")
    return f"{DID_KEY_SCHEME}{MULTIBASE_BASE58BTC_PREFIX}{base58btc_encode(prefix + bytes(pubkey))}"


def decode_did_key(did: str) -> DidKey:
    if not isinstance(did, str) or not did:
        raise InvalidDidKeyFormatError("did 必须非空 str")
    if not did.startswith(DID_KEY_SCHEME):
        raise InvalidDidKeyFormatError(f"did 必须以 '{DID_KEY_SCHEME}' 开头")
    identifier = did[len(DID_KEY_SCHEME):]
    if not identifier:
        raise InvalidDidKeyFormatError("did:key identifier 为空")
    if not identifier.startswith(MULTIBASE_BASE58BTC_PREFIX):
        raise InvalidDidKeyFormatError(
            f"did:key identifier 必须以 multibase '{MULTIBASE_BASE58BTC_PREFIX}' 开头"
        )
    b58_body = identifier[len(MULTIBASE_BASE58BTC_PREFIX):]
    try:
        raw = base58btc_decode(b58_body)
    except InvalidDidKeyFormatError:
        raise
    except Exception as e:
        raise InvalidDidKeyFormatError(f"base58btc 解码失败: {e}") from e
    if len(raw) < 2:
        raise InvalidDidKeyFormatError(f"did:key payload 太短 ({len(raw)}B)")
    prefix = bytes(raw[:2])
    pubkey = bytes(raw[2:])
    if prefix == X25519_PUB_MULTICODEC_PREFIX:
        if len(pubkey) != X25519_PUBKEY_SIZE:
            raise InvalidDidKeyFormatError(
                f"X25519 pubkey 必须 {X25519_PUBKEY_SIZE}B, 实际 {len(pubkey)}B"
            )
        return DidKey(did=did, pubkey=pubkey, multicodec_prefix=prefix)
    if prefix == ED25519_PUB_MULTICODEC_PREFIX:
        if len(pubkey) != X25519_PUBKEY_SIZE:
            raise InvalidDidKeyFormatError(f"Ed25519 pubkey 必须 32B, 实际 {len(pubkey)}B")
        return DidKey(did=did, pubkey=pubkey, multicodec_prefix=prefix)
    raise UnsupportedMulticodecError(
        f"did:key multicodec 0x{prefix.hex()} 本实现未支持 (只支持 X25519/Ed25519)"
    )


def did_key_to_pubkey(did: str) -> bytes:
    return decode_did_key(did).pubkey


def verify_did_key(did: str) -> bool:
    try:
        decode_did_key(did)
        return True
    except DidKeyError:
        return False


def generate_did_key_from_master(
    master_seed: bytes, *, index: int = 0
) -> tuple[str, PrivateKey, PublicKey]:
    if not isinstance(master_seed, (bytes, bytearray)) or len(master_seed) == 0:
        raise ValueError("master_seed 必须非空 bytes")
    if not isinstance(index, int) or index < 0:
        raise ValueError(f"index 必须 >= 0 int, 实际 {index}")
    seed_32 = derive_subkey(bytes(master_seed), _DID_KEY_PURPOSE, index=index)
    assert len(seed_32) == X25519_PRIVKEY_SEED_SIZE
    priv = PrivateKey(seed_32)
    pub = priv.public_key
    did = encode_did_key(pub.encode(), key_type="X25519")
    return did, priv, pub


def generate_did_key(master_key: bytes, *, index: int = 0) -> str:
    did, _priv, _pub = generate_did_key_from_master(master_key, index=index)
    return did


def derive_did_key_keypair(
    master_seed: bytes, *, index: int = 0
) -> tuple[PrivateKey, PublicKey]:
    _did, priv, pub = generate_did_key_from_master(master_seed, index=index)
    return priv, pub


__all__ = [
    "DID_KEY_SCHEME",
    "X25519_PUB_MULTICODEC_PREFIX",
    "ED25519_PUB_MULTICODEC_PREFIX",
    "X25519_PUBKEY_SIZE",
    "MULTIBASE_BASE58BTC_PREFIX",
    "DidKey",
    "DidKeyError",
    "InvalidDidKeyFormatError",
    "UnsupportedMulticodecError",
    "base58btc_encode",
    "base58btc_decode",
    "encode_did_key",
    "decode_did_key",
    "did_key_to_pubkey",
    "verify_did_key",
    "generate_did_key",
    "generate_did_key_from_master",
    "derive_did_key_keypair",
]
