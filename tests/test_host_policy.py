"""Tests for sisoul.p2p.host_policy — the cloud-refusal gate.

User red line: embedded kubo / GossipSub may run on mac / wsl / win only, never
on aws-* / cloud hosts. WSL2 must stay allowed despite its Microsoft DMI vendor.
"""

from __future__ import annotations

import pytest

from sisoul.p2p import host_policy as hp


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    # ensure a stray env var on the test host doesn't mask the logic under test
    monkeypatch.delenv(hp.ALLOW_CLOUD_P2P_ENV, raising=False)


def _patch_signals(monkeypatch, *, host="laptop", wsl=False, etc_cloud=False, dmi=""):
    monkeypatch.setattr(hp, "_hostname", lambda: host)
    monkeypatch.setattr(hp, "_is_wsl", lambda: wsl)
    monkeypatch.setattr(hp, "_etc_cloud_present", lambda: etc_cloud)
    monkeypatch.setattr(hp, "_dmi_vendor", lambda: dmi)


# ── allowed hosts (the user's own laptops) ───────────────────────────────────


def test_mac_laptop_allowed(monkeypatch):
    _patch_signals(monkeypatch, host="hx-macbook-14", wsl=False, etc_cloud=False, dmi="")
    assert hp.cloud_refusal_reason() is None
    assert hp.p2p_allowed() is True


def test_wsl_allowed_even_with_microsoft_dmi(monkeypatch):
    # WSL2 reports DMI vendor "Microsoft Corporation" — must NOT be refused.
    _patch_signals(
        monkeypatch, host="hx-ashui-wsl", wsl=True, etc_cloud=False, dmi="microsoft corporation"
    )
    assert hp.cloud_refusal_reason() is None
    assert hp.p2p_allowed() is True


# ── refused hosts (aws-* / cloud) ────────────────────────────────────────────


def test_aws_hostname_refused(monkeypatch):
    _patch_signals(monkeypatch, host="aws-us")
    reason = hp.cloud_refusal_reason()
    assert reason is not None and "aws-" in reason
    assert hp.p2p_allowed() is False


def test_ec2_instance_id_hostname_refused(monkeypatch):
    _patch_signals(monkeypatch, host="i-0abc123def456")
    reason = hp.cloud_refusal_reason()
    assert reason is not None and "i-" in reason


def test_etc_cloud_refused(monkeypatch):
    _patch_signals(monkeypatch, host="some-box", wsl=False, etc_cloud=True, dmi="")
    reason = hp.cloud_refusal_reason()
    assert reason is not None and "cloud" in reason.lower()


def test_dmi_amazon_refused(monkeypatch):
    _patch_signals(monkeypatch, host="some-box", wsl=False, etc_cloud=False, dmi="amazon ec2")
    reason = hp.cloud_refusal_reason()
    assert reason is not None and "amazon ec2" in reason


def test_dmi_gcp_refused(monkeypatch):
    _patch_signals(monkeypatch, host="some-box", wsl=False, etc_cloud=False, dmi="google compute engine")
    assert hp.cloud_refusal_reason() is not None


# ── override ─────────────────────────────────────────────────────────────────


def test_override_allows_cloud(monkeypatch):
    _patch_signals(monkeypatch, host="aws-us", etc_cloud=True, dmi="amazon ec2")
    monkeypatch.setenv(hp.ALLOW_CLOUD_P2P_ENV, "1")
    assert hp.cloud_refusal_reason() is None
    assert hp.p2p_allowed() is True


# ── kubo node honours the gate ───────────────────────────────────────────────


def test_kubo_subprocess_refused_on_cloud(monkeypatch):
    from sisoul.p2p.ipfs_kubo import IPFSCloudRefused, IPFSKuboNode

    monkeypatch.setattr(hp, "_hostname", lambda: "aws-us")
    node = IPFSKuboNode(mode="kubo-subprocess")
    with pytest.raises(IPFSCloudRefused):
        node.start_sync()


def test_kubo_mock_mode_unaffected_on_cloud(monkeypatch):
    # mock + external-daemon modes don't spawn anything, so the gate must not block them.
    from sisoul.p2p.ipfs_kubo import IPFSKuboNode

    monkeypatch.setattr(hp, "_hostname", lambda: "aws-us")
    node = IPFSKuboNode(mode="mock")
    node.start_sync()  # must not raise
    assert node.peer_id is not None


def test_kubo_subprocess_allowed_on_laptop_reaches_binary_check(monkeypatch):
    # On an allowed host the gate passes; without a binary it must raise the
    # *binary-not-found* error (IPFSKuboNotFound), NOT the cloud-refusal error.
    from sisoul.p2p.ipfs_kubo import IPFSCloudRefused, IPFSKuboNode, IPFSKuboNotFound

    monkeypatch.setattr(hp, "_hostname", lambda: "hx-macbook-14")
    monkeypatch.setattr(hp, "_is_wsl", lambda: False)
    monkeypatch.setattr(hp, "_etc_cloud_present", lambda: False)
    monkeypatch.setattr(hp, "_dmi_vendor", lambda: "")
    monkeypatch.setattr("sisoul.p2p.ipfs_kubo.find_kubo_binary", lambda *a, **k: None)
    node = IPFSKuboNode(mode="kubo-subprocess")
    with pytest.raises(IPFSKuboNotFound):
        node.start_sync()
    # sanity: it was NOT the cloud refusal
    assert not isinstance(IPFSKuboNotFound(), IPFSCloudRefused)


# ── real host (informational) ────────────────────────────────────────────────


def test_real_host_signals_consistent():
    # Whatever this host is, reason and p2p_allowed must agree.
    reason = hp.cloud_refusal_reason()
    assert (reason is None) == hp.p2p_allowed()
