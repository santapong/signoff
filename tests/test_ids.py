import time

from packages.domain.ids import make_experiment_id, make_id, make_session_id, parse_prefix


def test_ids_unique():
    ids = {make_id("ses") for _ in range(200)}
    assert len(ids) == 200


def test_ids_time_sortable():
    a = make_id("cyc")
    time.sleep(0.005)
    b = make_id("cyc")
    assert a < b  # lexicographic order matches creation order


def test_session_id_prefix():
    sid = make_session_id()
    assert sid.startswith("ses_")
    assert parse_prefix(sid) == "ses"


def test_experiment_id_format():
    assert make_experiment_id("cycle", 17) == "EXP-CYCLE-017"
