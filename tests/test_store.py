"""RedisStore against fakeredis — real commands, real semantics."""

from __future__ import annotations

import pytest


def test_round_trips_json_values(store):
    store.set("k", {"path": ["A", "B"], "n": 2})
    assert store.get("k") == {"path": ["A", "B"], "n": 2}


def test_missing_keys_read_as_none(store):
    assert store.get("nope") is None


def test_values_expire(store):
    store.set("k", "v", ttl=100)
    assert 0 < store._redis.ttl("k") <= 100


def test_set_uses_the_default_ttl_when_none_is_given(store):
    store.set("k", "v")
    assert 0 < store._redis.ttl("k") <= store.default_ttl


def test_delete_accepts_many_keys_and_tolerates_none(store):
    store.set("a", 1)
    store.set("b", 2)

    store.delete("a", "b", "never-existed")
    store.delete()

    assert store.get("a") is None and store.get("b") is None


def test_set_membership_answers_in_order(store):
    store.set_add("visited", "A", "C")
    assert store.set_contains("visited", ["A", "B", "C"]) == [True, False, True]


def test_set_membership_of_nothing_is_nothing(store):
    assert store.set_contains("visited", []) == []


def test_hash_fields_round_trip(store):
    store.hash_set("parents", {"B": "A", "C": "B"})

    assert store.hash_get("parents", "B") == "A"
    assert store.hash_get("parents", "missing") is None


def test_queue_is_first_in_first_out(store):
    store.queue_push("q", [{"page": "A"}, {"page": "B"}, {"page": "C"}])

    assert store.queue_pop("q", 2) == [{"page": "A"}, {"page": "B"}]
    assert store.queue_length("q") == 1


def test_queue_pop_returns_what_is_left(store):
    store.queue_push("q", [{"page": "A"}])
    assert store.queue_pop("q", 10) == [{"page": "A"}]
    assert store.queue_pop("q", 10) == []


@pytest.mark.parametrize("count", [0, -1])
def test_queue_pop_of_nothing_touches_nothing(store, count):
    store.queue_push("q", [{"page": "A"}])
    assert store.queue_pop("q", count) == []
    assert store.queue_length("q") == 1


def test_expire_sets_a_deadline_on_an_existing_key(store):
    store.set_add("visited", "A")
    store.expire("visited", 30)
    assert 0 < store._redis.ttl("visited") <= 30


def test_ping_reports_reachability(store):
    assert store.ping() is True
