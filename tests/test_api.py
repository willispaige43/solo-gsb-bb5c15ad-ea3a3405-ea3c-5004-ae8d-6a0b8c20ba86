"""End-to-end tests for the FastAPI /evaluate endpoint."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_evaluate_true():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"age": 20, "country": "CN", "tier": "gold"},
            "rules": {
                "is_adult": {"gte": ["age", 18]},
                "domestic": {"eq": ["country", "CN"]},
                "eligible": {
                    "all": [
                        {"ref": "is_adult"},
                        {"ref": "domestic"},
                        {"not": {"eq": ["tier", "banned"]}},
                    ]
                },
            },
            "target": "eligible",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "true"
    assert body["result_known"] is True
    assert body["missing_fields"] == []
    assert body["used_facts"] == ["age", "country", "tier"]


def test_evaluate_unknown_and_diagnostics():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"age": 20},
            "rules": {"eligible": {"all": [
                {"gte": ["age", 18]},
                {"eq": ["country", "CN"]},
            ]}},
            "target": "eligible",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "unknown"
    assert body["missing_fields"] == ["country"]
    assert body["used_facts"] == ["age", "country"]


def test_evaluate_short_circuit_stable():
    payload = {
        "facts": {"a": 0, "b": 2},
        "target": {"all": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]},
    }
    r1 = client.post("/evaluate", json=payload).json()
    r2 = client.post("/evaluate", json=payload).json()
    assert r1 == r2
    assert r1["result"] == "false"
    paths = [p["path"] for p in r1["hit_paths"]]
    assert "$target/all[1]" not in paths


def test_missing_ref_returns_422():
    r = client.post(
        "/evaluate",
        json={"facts": {}, "rules": {"a": {"ref": "ghost"}}, "target": "a"},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "ghost" in r.json()["detail"]


def test_cycle_returns_422():
    r = client.post(
        "/evaluate",
        json={
            "facts": {},
            "rules": {"a": {"ref": "b"}, "b": {"ref": "a"}},
            "target": "a",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "cyclic" in r.json()["detail"]


def test_malformed_body_returns_422():
    r = client.post("/evaluate", json={"facts": "not-an-object"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"


def test_inline_target():
    r = client.post(
        "/evaluate",
        json={
            "facts": {"x": 5},
            "target": {"any": [{"gt": ["x", 10]}, {"gt": ["x", 1]}]},
        },
    )
    assert r.status_code == 200
    assert r.json()["result"] == "true"


# --------------------------------------------------------------------------- #
# POST /compare
# --------------------------------------------------------------------------- #


def test_compare_result_changed():
    r = client.post(
        "/compare",
        json={
            "baseline_facts": {"age": 17, "country": "CN"},
            "candidate_facts": {"age": 20, "country": "CN"},
            "rules": {
                "is_adult": {"gte": ["age", 18]},
                "eligible": {"all": [
                    {"ref": "is_adult"},
                    {"eq": ["country", "CN"]},
                ]},
            },
            "target": "eligible",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["baseline"]["result"] == "false"
    assert body["candidate"]["result"] == "true"
    assert body["result_changed"] is True
    changed = {e["path"] for e in body["hit_paths_result_changed"]}
    assert "$target/all[0]/ref:is_adult" in changed
    assert "$target" in changed
    # Baseline short-circuited before the country check; candidate reached it.
    assert [e["path"] for e in body["hit_paths_added"]] == ["$target/all[1]"]
    assert body["hit_paths_removed"] == []


def test_compare_no_change():
    r = client.post(
        "/compare",
        json={
            "baseline_facts": {"age": 20},
            "candidate_facts": {"age": 20},
            "target": {"gte": ["age", 18]},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result_changed"] is False
    assert body["baseline"] == body["candidate"]
    assert body["hit_paths_added"] == []
    assert body["hit_paths_removed"] == []
    assert body["hit_paths_result_changed"] == []
    assert body["used_facts_added"] == []
    assert body["used_facts_removed"] == []
    assert body["missing_fields_added"] == []
    assert body["missing_fields_removed"] == []


def test_compare_fact_and_missing_diffs():
    r = client.post(
        "/compareset",
        json={},
    )
    assert r.status_code == 404  # sanity: only /evaluate and /compare exist
    r = client.post(
        "/compare",
        json={
            "baseline_facts": {"a": 0, "b": 2},
            "candidate_facts": {"a": 1},
            "target": {"all": [{"eq": ["a", 1]}, {"eq": ["b", 2]}]},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["baseline"]["result"] == "false"
    assert body["candidate"]["result"] == "unknown"
    assert body["result_changed"] is True
    assert body["used_facts_added"] == ["b"]
    assert body["used_facts_removed"] == []
    assert body["missing_fields_added"] == ["b"]
    assert body["missing_fields_removed"] == []


def test_compare_is_repeatable():
    payload = {
        "baseline_facts": {"x": 1},
        "candidate_facts": {"x": 2, "y": 5},
        "rules": {"r": {"any": [{"eq": ["x", 1]}, {"gt": ["y", 3]}]}},
        "target": "r",
    }
    r1 = client.post("/compare", json=payload)
    r2 = client.post("/compare", json=payload)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()


def test_compare_missing_ref_returns_422():
    r = client.post(
        "/compare",
        json={
            "baseline_facts": {},
            "candidate_facts": {},
            "rules": {"a": {"ref": "ghost"}},
            "target": "a",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "ghost" in r.json()["detail"]


def test_compare_cycle_returns_422():
    r = client.post(
        "/compare",
        json={
            "baseline_facts": {},
            "candidate_facts": {},
            "rules": {"a": {"ref": "b"}, "b": {"ref": "a"}},
            "target": "a",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"] == "rule_error"
    assert "cyclic" in r.json()["detail"]


def test_compare_malformed_body_returns_422():
    # facts must be objects
    r = client.post(
        "/compare",
        json={"baseline_facts": "not-an-object", "target": {"eq": ["x", 1]}},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"
    # target is required
    r = client.post(
        "/compare",
        json={"baseline_facts": {}, "candidate_facts": {}},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_request"
