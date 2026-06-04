"""PQXDH (X25519 + ML-KEM-1024 hybrid) handshake.

KEM backend resolution: liboqs-python -> kyber-py -> shim.
Shared secret = HKDF-SHA512(b"sisoul-pqxdh-v1",
    ikm = dh_ik_spk || dh_ek_ik || dh_ek_spk || mlkem_ss,
    info = init_did || resp_did, L=32).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Final, Literal

import nacl.bindings as nb
from nacl.signing import SigningKey, VerifyKey

PQXDH_MODE = Literal["real", "shim"]

_MLKEM_PUB_LEN: Final[int] = 1568
_MLKEM_CT_LEN: Final[int] = 1568
_MLKEM_SS_LEN: Final[int] = 32


def _detect_backend() -> str:
    # liboqs only if the shared lib is already present; bare `import oqs`
    # triggers a 5-second blocking auto-install that fails on hosts lacking cmake.
    if os.environ.get("SISOUL_DISABLE_LIBOQS") != "1":
        try:
            import importlib.util
            if importlib.util.find_spec("oqs") is not None:
                import oqs  # type: ignore
                with oqs.KeyEncapsulation("ML-KEM-1024"):
                    return "liboqs"
        except Exception:
            pass
    try:
        from kyber_py.ml_kem import ML_KEM_1024  # noqa: F401
        return "kyber-py"
    except Exception:
        pass
    return "shim"


_BACKEND = _detect_backend()


def pqxdh_mode() -> PQXDH_MODE:
    return "real" if _BACKEND in ("liboqs", "kyber-py") else "shim"


def _mlkem_keygen() -> tuple[bytes, bytes]:
    if _BACKEND == "liboqs":
        import oqs
        kem = oqs.KeyEncapsulation("ML-KEM-1024")
        ek = kem.generate_keypair()
        dk = kem.export_secret_key()
        kem.free()
        return ek, dk
    if _BACKEND == "kyber-py":
        from kyber_py.ml_kem import ML_KEM_1024
        ek, dk = ML_KEM_1024.keygen()
        return bytes(ek), bytes(dk)
    # shim: not cryptographically secure; self-consistent for round-trips only.
    sk_seed = os.urandom(32)
    ek = hashlib.shake_256(b"shim-ek\x00" + sk_seed).digest(_MLKEM_PUB_LEN)
    dk = sk_seed + hashlib.shake_256(b"shim-dk\x00" + sk_seed).digest(3168 - 32)
    return ek, dk


def _mlkem_encaps(ek: bytes) -> tuple[bytes, bytes]:
    if len(ek) != _MLKEM_PUB_LEN:
        raise ValueError(f"ML-KEM-1024 public key must be {_MLKEM_PUB_LEN} bytes")
    if _BACKEND == "liboqs":
        import oqs
        with oqs.KeyEncapsulation("ML-KEM-1024") as kem:
            ct, ss = kem.encap_secret(ek)
            return ct, ss
    if _BACKEND == "kyber-py":
        from kyber_py.ml_kem import ML_KEM_1024
        ss, ct = ML_KEM_1024.encaps(ek)
        return bytes(ct), bytes(ss)
    randomness = os.urandom(32)
    ek_mask = hashlib.sha256(b"shim-ek-mask\x00" + ek).digest()
    tag = bytes(a ^ b for a, b in zip(randomness, ek_mask))
    ct = tag + hashlib.shake_256(b"shim-ct\x00" + randomness + ek).digest(_MLKEM_CT_LEN - 32)
    ss = hashlib.sha256(b"shim-ss\x00" + randomness).digest()
    return ct, ss


def _mlkem_decaps(dk: bytes, ct: bytes) -> bytes:
    if len(ct) != _MLKEM_CT_LEN:
        raise ValueError(f"ML-KEM-1024 ciphertext must be {_MLKEM_CT_LEN} bytes")
    if _BACKEND == "liboqs":
        import oqs
        with oqs.KeyEncapsulation("ML-KEM-1024", secret_key=dk) as kem:
            return kem.decap_secret(ct)
    if _BACKEND == "kyber-py":
        from kyber_py.ml_kem import ML_KEM_1024
        return bytes(ML_KEM_1024.decaps(dk, ct))
    sk_seed = dk[:32]
    ek = hashlib.shake_256(b"shim-ek\x00" + sk_seed).digest(_MLKEM_PUB_LEN)
    ek_mask = hashlib.sha256(b"shim-ek-mask\x00" + ek).digest()
    randomness = bytes(a ^ b for a, b in zip(ct[:32], ek_mask))
    return hashlib.sha256(b"shim-ss\x00" + randomness).digest()


@dataclass
class PreKeyBundle:
    """Public pre-key bundle published periodically by each peer."""

    did: str
    x25519_pub: bytes
    mlkem_pub: bytes
    signed_pre_key_pub: bytes
    signature: bytes
    issued_at: int
    pqxdh_mode: PQXDH_MODE

    def to_dict(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "x25519_pub": self.x25519_pub.hex(),
            "mlkem_pub": self.mlkem_pub.hex(),
            "signed_pre_key_pub": self.signed_pre_key_pub.hex(),
            "signature": self.signature.hex(),
            "issued_at": self.issued_at,
            "pqxdh_mode": self.pqxdh_mode,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PreKeyBundle":
        return cls(
            did=d["did"],
            x25519_pub=bytes.fromhex(d["x25519_pub"]),
            mlkem_pub=bytes.fromhex(d["mlkem_pub"]),
            signed_pre_key_pub=bytes.fromhex(d["signed_pre_key_pub"]),
            signature=bytes.fromhex(d["signature"]),
            issued_at=int(d["issued_at"]),
            pqxdh_mode=d.get("pqxdh_mode", "real"),
        )

    @classmethod
    def from_json(cls, s: str) -> "PreKeyBundle":
        return cls.from_dict(json.loads(s))


@dataclass
class LocalKeyMaterial:
    """Full key material kept locally (never serialized to peers)."""

    did: str
    ed25519_sign_priv: bytes
    x25519_identity_priv: bytes
    x25519_identity_pub: bytes
    x25519_spk_priv: bytes
    x25519_spk_pub: bytes
    mlkem_priv: bytes
    mlkem_pub: bytes
    bundle: PreKeyBundle


class PQXDHError(Exception):
    """PQXDH handshake error."""


class BadSignatureError(PQXDHError):
    """PreKeyBundle Ed25519 signature failed verification."""


def _hkdf_sha512(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha512).digest()
    out = b""
    prev = b""
    counter = 1
    while len(out) < length:
        prev = hmac.new(prk, prev + info + bytes([counter]), hashlib.sha512).digest()
        out += prev
        counter += 1
    return out[:length]


def generate_pre_key_bundle(did: str, ed25519_sign_priv: bytes | None = None) -> LocalKeyMaterial:
    """Generate a fresh PreKeyBundle + matching local secrets."""
    if not did:
        raise ValueError("did required")
    if ed25519_sign_priv is None:
        sk = SigningKey.generate()
    else:
        if len(ed25519_sign_priv) != 32:
            raise ValueError("ed25519_sign_priv must be 32-byte seed")
        sk = SigningKey(ed25519_sign_priv)
    ed25519_seed = bytes(sk.encode())

    ix_priv = os.urandom(32)
    ix_pub = nb.crypto_scalarmult_base(ix_priv)
    spk_priv = os.urandom(32)
    spk_pub = nb.crypto_scalarmult_base(spk_priv)
    mlkem_pub, mlkem_priv = _mlkem_keygen()

    sig_payload = ix_pub + mlkem_pub + spk_pub
    signature = sk.sign(sig_payload).signature

    bundle = PreKeyBundle(
        did=did,
        x25519_pub=ix_pub,
        mlkem_pub=mlkem_pub,
        signed_pre_key_pub=spk_pub,
        signature=signature,
        issued_at=int(time.time()),
        pqxdh_mode=pqxdh_mode(),
    )
    return LocalKeyMaterial(
        did=did,
        ed25519_sign_priv=ed25519_seed,
        x25519_identity_priv=ix_priv,
        x25519_identity_pub=ix_pub,
        x25519_spk_priv=spk_priv,
        x25519_spk_pub=spk_pub,
        mlkem_priv=mlkem_priv,
        mlkem_pub=mlkem_pub,
        bundle=bundle,
    )


def verify_bundle(bundle: PreKeyBundle, signer_verify_key: bytes) -> None:
    """Verify Ed25519 signature on ``bundle``. Raises ``BadSignatureError``."""
    if len(signer_verify_key) != 32:
        raise BadSignatureError("verify key must be 32 bytes")
    payload = bundle.x25519_pub + bundle.mlkem_pub + bundle.signed_pre_key_pub
    try:
        VerifyKey(signer_verify_key).verify(payload, bundle.signature)
    except Exception as exc:
        raise BadSignatureError(str(exc)) from exc


def complete_handshake_initiator(
    local: LocalKeyMaterial,
    remote_bundle: PreKeyBundle,
    remote_verify_key: bytes | None = None,
) -> tuple[bytes, bytes, bytes]:
    """Initiator side. Returns (shared_secret, ephemeral_x25519_pub, mlkem_ct)."""
    if remote_verify_key is not None:
        verify_bundle(remote_bundle, remote_verify_key)
    ek_priv = os.urandom(32)
    ek_pub = nb.crypto_scalarmult_base(ek_priv)
    dh1 = nb.crypto_scalarmult(local.x25519_identity_priv, remote_bundle.signed_pre_key_pub)
    dh2 = nb.crypto_scalarmult(ek_priv, remote_bundle.x25519_pub)
    dh3 = nb.crypto_scalarmult(ek_priv, remote_bundle.signed_pre_key_pub)
    ct, pq_ss = _mlkem_encaps(remote_bundle.mlkem_pub)
    ikm = dh1 + dh2 + dh3 + pq_ss
    info = (local.did + "|" + remote_bundle.did).encode()
    shared = _hkdf_sha512(b"sisoul-pqxdh-v1", ikm, info, 32)
    return shared, ek_pub, ct


def complete_handshake_responder(
    local: LocalKeyMaterial,
    initiator_did: str,
    initiator_identity_pub: bytes,
    initiator_ephemeral_pub: bytes,
    mlkem_ciphertext: bytes,
) -> bytes:
    """Responder side. Returns 32-byte shared secret matching initiator's."""
    dh1 = nb.crypto_scalarmult(local.x25519_spk_priv, initiator_identity_pub)
    dh2 = nb.crypto_scalarmult(local.x25519_identity_priv, initiator_ephemeral_pub)
    dh3 = nb.crypto_scalarmult(local.x25519_spk_priv, initiator_ephemeral_pub)
    pq_ss = _mlkem_decaps(local.mlkem_priv, mlkem_ciphertext)
    ikm = dh1 + dh2 + dh3 + pq_ss
    info = (initiator_did + "|" + local.did).encode()
    return _hkdf_sha512(b"sisoul-pqxdh-v1", ikm, info, 32)


__all__ = [
    "PreKeyBundle",
    "LocalKeyMaterial",
    "PQXDHError",
    "BadSignatureError",
    "generate_pre_key_bundle",
    "verify_bundle",
    "complete_handshake_initiator",
    "complete_handshake_responder",
    "pqxdh_mode",
]
