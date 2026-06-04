"""Tests for sisoul.friend.petname (P2-CD)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.friend import friend_app
from sisoul.friend.petname import (
    DEFAULT_PETNAME_PATH,
    PetnameError,
    PetnameStore,
    format_did_short,
)

runner = CliRunner()

EXAMPLE_DID = "did:key:z6MkpTHR8VNsBxYAAWHut2Geo2LWzKE9bUFr3R4nC9pYjkbA"
EXAMPLE_DID2 = "did:key:z6MkfvxPGoBnVPzgFxF2vKZLshcjL7CmZjxAdYbk1XfBNNz6"


class TestPetnameStore:
    def test_set_get_roundtrip(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        st.set(EXAMPLE_DID, "Alice")
        # 新实例 reload from disk → 同 value
        st2 = PetnameStore(path=tmp_path / "p.json").load()
        assert st2.get(EXAMPLE_DID) == "Alice"
        assert len(st2) == 1

    def test_get_default_when_missing(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json").load()
        assert st.get(EXAMPLE_DID) is None
        assert st.get(EXAMPLE_DID, default="anon") == "anon"

    def test_overwrite(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        st.set(EXAMPLE_DID, "Alice")
        st.set(EXAMPLE_DID, "AliceV2")
        assert st.get(EXAMPLE_DID) == "AliceV2"

    def test_remove_existing_and_missing(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        st.set(EXAMPLE_DID, "Alice")
        assert st.remove(EXAMPLE_DID) is True
        assert st.get(EXAMPLE_DID) is None
        # remove again → False
        assert st.remove(EXAMPLE_DID) is False

    def test_list_all_multi(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        st.set(EXAMPLE_DID, "Alice")
        st.set(EXAMPLE_DID2, "Bob")
        items = st.list_all()
        assert items == {EXAMPLE_DID: "Alice", EXAMPLE_DID2: "Bob"}

    def test_invalid_did_rejected(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        with pytest.raises(PetnameError):
            st.set("notadid", "Alice")
        with pytest.raises(PetnameError):
            st.set("", "Alice")

    def test_invalid_petname_rejected(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        with pytest.raises(PetnameError):
            st.set(EXAMPLE_DID, "")
        with pytest.raises(PetnameError):
            st.set(EXAMPLE_DID, "x" * 100)
        with pytest.raises(PetnameError):
            st.set(EXAMPLE_DID, "bad\x01ctrl")

    def test_corrupted_json_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "p.json"
        p.write_text("{not valid json", encoding="utf-8")
        st = PetnameStore(path=p)
        with pytest.raises(PetnameError):
            st.load()

    def test_empty_file_treated_as_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "p.json"
        p.write_text("", encoding="utf-8")
        st = PetnameStore(path=p).load()
        assert len(st) == 0

    def test_contains(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        st.set(EXAMPLE_DID, "Alice")
        assert EXAMPLE_DID in st
        assert "did:key:z9notexist" not in st

    def test_display_name_falls_back_to_short(self, tmp_path: Path) -> None:
        st = PetnameStore(path=tmp_path / "p.json")
        # 没 set → fallback short
        s = st.display_name(EXAMPLE_DID)
        assert "…" in s
        assert s.startswith("did:key:")
        # set 后 → petname
        st.set(EXAMPLE_DID, "Alice")
        assert st.display_name(EXAMPLE_DID) == "Alice"

    def test_format_did_short(self) -> None:
        s = format_did_short(EXAMPLE_DID)
        assert "…" in s
        # 短 did 不缩
        assert format_did_short("did:x") == "did:x"

    def test_default_path_is_under_home(self) -> None:
        assert str(DEFAULT_PETNAME_PATH).endswith(".sisoul/petnames.json")


class TestPetnameCLI:
    def test_cli_set_and_list(self, tmp_path: Path) -> None:
        store = tmp_path / "p.json"
        r = runner.invoke(
            friend_app,
            ["petname", "set", EXAMPLE_DID, "Alice", "--store", str(store)],
        )
        assert r.exit_code == 0, r.output
        assert "Alice" in r.output
        r2 = runner.invoke(
            friend_app, ["petname", "list", "--store", str(store), "--json"]
        )
        assert r2.exit_code == 0, r2.output
        obj = json.loads(r2.output)
        assert obj[EXAMPLE_DID] == "Alice"

    def test_cli_rm(self, tmp_path: Path) -> None:
        store = tmp_path / "p.json"
        runner.invoke(
            friend_app,
            ["petname", "set", EXAMPLE_DID, "Alice", "--store", str(store)],
        )
        r = runner.invoke(
            friend_app, ["petname", "rm", EXAMPLE_DID, "--store", str(store)]
        )
        assert r.exit_code == 0, r.output
        # 删不存在的 → exit 1
        r2 = runner.invoke(
            friend_app, ["petname", "rm", EXAMPLE_DID, "--store", str(store)]
        )
        assert r2.exit_code == 1

    def test_cli_list_empty(self, tmp_path: Path) -> None:
        store = tmp_path / "p.json"
        r = runner.invoke(friend_app, ["petname", "list", "--store", str(store)])
        assert r.exit_code == 0
        assert "本地无 petname" in r.output

    def test_cli_set_invalid_did(self, tmp_path: Path) -> None:
        store = tmp_path / "p.json"
        r = runner.invoke(
            friend_app,
            ["petname", "set", "garbage", "Alice", "--store", str(store)],
        )
        assert r.exit_code == 1
