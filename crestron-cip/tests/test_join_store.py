"""Tests for the join store.

The store's job is to keep two things apart: what the processor reports,
which is refreshed constantly, and what the user decided, which must never
be overwritten by a rediscovery, a restart or a program reload.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from join_store import (  # noqa: E402
    KIND_BINARY_SENSOR, KIND_BUTTON, KIND_IGNORE, KIND_NUMBER, KIND_SENSOR,
    KIND_SWITCH, JoinStore,
)


@pytest.fixture
def store(tmp_path):
    return JoinStore(path=tmp_path / "joins.json")


def test_observing_a_join_records_it(store):
    join = store.observe("cp4", "a", 7, 1234)
    assert join.store_key == "cp4/a7"
    assert join.value == 1234
    assert join.updates == 1
    assert store.joins["cp4/a7"] is join


def test_a_second_update_does_not_duplicate(store):
    store.observe("cp4", "a", 7, 1234)
    store.observe("cp4", "a", 7, 4321)
    assert len(store.joins) == 1
    assert store.joins["cp4/a7"].value == 4321
    assert store.joins["cp4/a7"].updates == 2


def test_the_same_join_on_two_processors_is_two_joins(store):
    """Join numbers only mean anything per panel."""
    store.observe("cp4", "a", 7, 1)
    store.observe("dmps", "a", 7, 2)
    assert len(store.joins) == 2


def test_rediscovery_never_overwrites_user_configuration(store):
    store.observe("cp4", "d", 14, True)
    store.configure("cp4", "d14", name="Function 2 Mute",
                    kind=KIND_SWITCH, enabled=True)
    store.observe("cp4", "d", 14, False)

    cfg = store.joins["cp4/d14"].config
    assert cfg.name == "Function 2 Mute"
    assert cfg.kind == KIND_SWITCH
    assert cfg.enabled is True
    assert store.joins["cp4/d14"].value is False


def test_configuration_survives_a_save_and_load(tmp_path):
    path = tmp_path / "joins.json"
    first = JoinStore(path=path)
    first.observe("cp4", "s", 3, "Zone 1")
    first.configure("cp4", "s3", name="Zone name", enabled=True)
    first.save()

    second = JoinStore(path=path)
    second.load()
    assert second.joins["cp4/s3"].config.name == "Zone name"
    assert second.joins["cp4/s3"].config.enabled is True
    assert second.joins["cp4/s3"].value == "Zone 1"


def test_a_missing_store_loads_empty(tmp_path):
    store = JoinStore(path=tmp_path / "nothing.json")
    store.load()
    assert store.joins == {}


def test_a_corrupt_store_does_not_raise(tmp_path):
    path = tmp_path / "joins.json"
    path.write_text("{not json")
    store = JoinStore(path=path)
    store.load()
    assert store.joins == {}


def test_a_malformed_entry_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "joins.json"
    path.write_text('{"joins": {"cp4/a7": {"signal": "a"},'
                    ' "cp4/a8": {"processor":"cp4","key":"a8",'
                    '"signal":"a","number":8}}}')
    store = JoinStore(path=path)
    store.load()
    assert set(store.joins) == {"cp4/a8"}


@pytest.mark.parametrize("signal,kind", [
    ("d", KIND_SWITCH), ("d", KIND_BUTTON), ("d", KIND_BINARY_SENSOR),
    ("a", KIND_SENSOR), ("a", KIND_NUMBER),
    ("s", KIND_SENSOR),
])
def test_sensible_kinds_are_accepted(store, signal, kind):
    store.observe("cp4", signal, 1, None)
    store.configure("cp4", f"{signal}1", kind=kind)
    assert store.joins[f"cp4/{signal}1"].config.kind == kind


@pytest.mark.parametrize("signal,kind", [
    ("a", KIND_SWITCH),      # an analog is not a switch
    ("d", KIND_NUMBER),      # a digital is not a number
    ("s", KIND_BUTTON),      # a serial is not a button
])
def test_nonsensical_kinds_are_refused(store, signal, kind):
    store.observe("cp4", signal, 1, None)
    with pytest.raises(ValueError, match="cannot be"):
        store.configure("cp4", f"{signal}1", kind=kind)


def test_an_unknown_kind_is_refused(store):
    store.observe("cp4", "d", 1, True)
    with pytest.raises(ValueError, match="unknown kind"):
        store.configure("cp4", "d1", kind="teleporter")


def test_configuring_an_unknown_join_raises(store):
    with pytest.raises(KeyError):
        store.configure("cp4", "d99", name="nope")


def test_scale_cannot_be_zero(store):
    """Dividing by it later would be a crash on every update."""
    store.observe("cp4", "a", 1, 100)
    with pytest.raises(ValueError, match="zero"):
        store.configure("cp4", "a1", scale=0)


def test_only_enabled_joins_are_exposed(store):
    store.observe("cp4", "d", 1, True)
    store.observe("cp4", "d", 2, True)
    store.configure("cp4", "d1", enabled=True)
    assert [j.key for j in store.exposed()] == ["d1"]


def test_ignored_joins_are_not_exposed_even_when_enabled(store):
    """Ignore keeps a join out of HA without forgetting it."""
    store.observe("cp4", "d", 1, True)
    store.configure("cp4", "d1", enabled=True, kind=KIND_IGNORE)
    assert store.exposed() == []


def test_pruning_forgets_untouched_joins(store):
    store.observe("cp4", "d", 1, True)
    store.observe("cp4", "d", 2, True)
    removed = store.prune("cp4", seen_keys={"d1"})
    assert removed == 1
    assert set(store.joins) == {"cp4/d1"}


def test_pruning_keeps_anything_the_user_touched(store):
    """A program reload can drop a join briefly; losing the naming is worse."""
    store.observe("cp4", "d", 1, True)
    store.observe("cp4", "d", 2, True)
    store.observe("cp4", "d", 3, True)
    store.configure("cp4", "d2", name="Projector Lift")
    store.configure("cp4", "d3", enabled=True)

    removed = store.prune("cp4", seen_keys=set())
    assert removed == 1
    assert set(store.joins) == {"cp4/d2", "cp4/d3"}


def test_pruning_leaves_other_processors_alone(store):
    store.observe("cp4", "d", 1, True)
    store.observe("dmps", "d", 1, True)
    store.prune("cp4", seen_keys=set())
    assert set(store.joins) == {"dmps/d1"}


def test_default_kind_follows_the_signal(store):
    store.observe("cp4", "d", 1, True)
    store.observe("cp4", "a", 1, 5)
    store.observe("cp4", "s", 1, "x")
    assert store.joins["cp4/d1"].to_dict()["kind"] == KIND_BINARY_SENSOR
    assert store.joins["cp4/a1"].to_dict()["kind"] == KIND_SENSOR
    assert store.joins["cp4/s1"].to_dict()["kind"] == KIND_SENSOR
