"""Profile identity, geometry and immutable revision boundary tests."""
import pytest

from packages.domain.profiles import ProfileStore, validate_profile


def test_defaults_are_hashed_and_repeatable():
    p = validate_profile({"profile_id": "bench-1"})
    assert p["schema_version"] == 2
    assert validate_profile(p) == p
    assert p["detector"]["method"] == "aruco"


def test_revision_is_immutable_and_new_revision_is_independent(tmp_path):
    store = ProfileStore(tmp_path)
    first = store.put({"profile_id": "bench"})
    assert store.put(first) == first
    changed = {**first, "name": "changed"}
    changed.pop("profile_hash")
    with pytest.raises(ValueError, match="immutable"):
        store.put(changed)
    next_revision = store.put({"profile_id": "bench", "name": "changed"})
    assert next_revision["revision"] == 2
    assert store.get("bench", 1) == first
    assert store.list() == [next_revision]


@pytest.mark.parametrize("value", ["../outside", "a/b", "", "a\\b"])
def test_identifiers_do_not_escape_store(value, tmp_path):
    with pytest.raises(ValueError):
        ProfileStore(tmp_path).put({"profile_id": value})


def test_bad_geometry_and_hash_are_rejected():
    with pytest.raises(ValueError, match="inside"):
        validate_profile({"profile_id": "bench", "detector": {"home": [.9, .9, .5, .5]}})
    with pytest.raises(ValueError, match="overlap"):
        validate_profile({"profile_id": "bench", "detector": {"home": [.1, .6, .2, .2]}})
    p = validate_profile({"profile_id": "bench"})
    p["camera"]["fps"] = 20
    with pytest.raises(ValueError, match="profile_hash"):
        validate_profile(p)


def test_nonfinite_and_boolean_config_values_fail():
    with pytest.raises(ValueError):
        validate_profile({"profile_id": "bench", "camera": {"fps": True}})
    with pytest.raises(ValueError):
        validate_profile({"profile_id": "bench", "detector": {"home_dwell_ms": float("nan")}})
