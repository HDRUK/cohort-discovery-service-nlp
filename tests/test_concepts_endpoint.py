from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from resolvers.medcat_client import MedCATClient

client = TestClient(app)


def _make_row(concept_id, name, category, match_score, ncollections, count, cnt=None, children=None, collection_score=0):
    return {
        "concept_id": concept_id,
        "name": name,
        "category": category,
        "match_score": match_score,
        "collection_score": collection_score,
        "ncollections": ncollections,
        "count": Decimal(str(count)) if count is not None else None,
        "cnt": cnt if cnt is not None else 1,
        "children": children,
    }


def _mock_engine(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cnt = rows[0]["cnt"] if rows else 0
    cursor.fetchone.return_value = {"cnt": cnt}
    raw_conn = MagicMock()
    raw_conn.cursor.return_value = cursor
    mock_engine = MagicMock()
    mock_engine.raw_connection.return_value = raw_conn
    return mock_engine, raw_conn


def test_search_by_concept_name_returns_matching_rows():
    rows = [
        _make_row(24006, "Sickle cell-hemoglobin C disease", "Condition", 500, 1, 10, cnt=2),
        _make_row(24007, "Sickle cell-thalassemia disease", "Condition", 500, 1, 5, cnt=2),
    ]
    mock_engine, _ = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"concept_name": ["sickle"], "include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    names = [r["name"] for r in body["data"]]
    assert "Sickle cell-hemoglobin C disease" in names
    assert "Sickle cell-thalassemia disease" in names


def test_search_by_concept_id_returns_exact_match():
    rows = [_make_row(24006, "Sickle cell-hemoglobin C disease", "Condition", 1000, 1, 10, cnt=1)]
    mock_engine, _ = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"concept_id": [24006], "include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["data"][0]["concept_id"] == 24006
    assert body["data"][0]["match_score"] == 1000


def test_domain_filter_is_forwarded():
    rows = [_make_row(3027018, "Heart rate", "Measurement", 0, 1, 20, cnt=1)]
    mock_engine, raw_conn = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"domain": "Measurement", "include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["category"] == "Measurement"

    # verify domain was included in the SQL call
    call_args = raw_conn.cursor.return_value.execute.call_args
    sql, bindings = call_args[0]
    assert "d.domain_id = %s" in sql
    assert "measurement" in bindings


def test_collection_filter_applied_when_flag_set():
    rows = [_make_row(3027018, "Heart rate", "Measurement", 0, 1, 20, cnt=1)]
    mock_engine, raw_conn = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post(
            "/concepts/search",
            json={
                "collection_ids": [2],
                "use_collection_filter": True,
                "include_ancestors": False,
            },
        )

    assert response.status_code == 200
    call_args = raw_conn.cursor.return_value.execute.call_args
    sql, bindings = call_args[0]
    assert "d.collection_id IN" in sql
    assert 2 in bindings


def test_collection_filter_not_applied_when_flag_false():
    rows = [_make_row(3027018, "Heart rate", "Measurement", 0, 1, 20, cnt=1)]
    mock_engine, raw_conn = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post(
            "/concepts/search",
            json={
                "collection_ids": [2],
                "use_collection_filter": False,
                "include_ancestors": False,
            },
        )

    assert response.status_code == 200
    call_args = raw_conn.cursor.return_value.execute.call_args
    sql, _bindings = call_args[0]
    where_clause = sql.split("WHERE", 1)[1].split("GROUP BY")[0] if "WHERE" in sql else ""
    assert "d.collection_id IN" not in where_clause


def test_no_search_params_returns_all_rows():
    rows = [
        _make_row(1, "Concept A", "Condition", 0, 1, 10, cnt=2),
        _make_row(2, "Concept B", "Measurement", 0, 1, 5, cnt=2),
    ]
    mock_engine, _ = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["data"]) == 2


def test_pagination_slices_correctly():
    rows = [
        _make_row(24007, "Sickle cell-thalassemia disease", "Condition", 0, 1, 5, cnt=4),
        _make_row(24006, "Sickle cell-hemoglobin C disease", "Condition", 0, 1, 10, cnt=4),
    ]
    mock_engine, _ = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post(
            "/concepts/search",
            json={"page": 2, "per_page": 2, "include_ancestors": False},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["current_page"] == 2
    assert body["per_page"] == 2
    assert body["last_page"] == 2
    assert len(body["data"]) == 2


def test_include_ancestors_false_skips_children_join():
    rows = [_make_row(1, "Diabetes", "Condition", 0, 1, 10, cnt=1)]
    mock_engine, raw_conn = _mock_engine(rows)
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    call_args = raw_conn.cursor.return_value.execute.call_args
    sql, _bindings = call_args[0]
    assert "concept_ancestors" not in sql
    assert body["data"][0]["children"] == []


def test_include_ancestors_true_attaches_children():
    rows = [_make_row(320128, "Essential hypertension", "Condition", 1000, 1, 50, cnt=1)]
    mock_engine, _ = _mock_engine(rows)
    store = app.state.resolver_store
    with patch.object(app.state.sql_resolver, "_engine", mock_engine), patch.object(
        store, "ancestor_map", {320128: [99]}
    ), patch.object(
        store, "concepts_by_id",
        {99: {"concept_id": 99, "name": "Child concept", "category": "Condition"}},
    ):
        response = client.post(
            "/concepts/search",
            json={"concept_id": [320128], "include_ancestors": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"][0]["children"]) == 1
    assert body["data"][0]["children"][0]["concept_id"] == 99
    assert body["data"][0]["children"][0]["name"] == "Child concept"


def test_include_ancestors_true_drops_ids_absent_from_concepts_by_id():
    rows = [_make_row(320128, "Essential hypertension", "Condition", 1000, 1, 50, cnt=1)]
    mock_engine, _ = _mock_engine(rows)
    store = app.state.resolver_store
    with patch.object(app.state.sql_resolver, "_engine", mock_engine), patch.object(
        store, "ancestor_map", {320128: [99, 12345]}
    ), patch.object(
        store, "concepts_by_id",
        {99: {"concept_id": 99, "name": "Child concept", "category": "Condition"}},
    ):
        response = client.post(
            "/concepts/search",
            json={"concept_id": [320128], "include_ancestors": True},
        )

    assert response.status_code == 200
    body = response.json()
    # 12345 has no concepts_by_id entry, so it is silently dropped.
    assert [c["concept_id"] for c in body["data"][0]["children"]] == [99]


def test_include_ancestors_true_reduced_mode_returns_empty_children():
    rows = [_make_row(320128, "Essential hypertension", "Condition", 1000, 1, 50, cnt=1)]
    mock_engine, _ = _mock_engine(rows)
    store = app.state.resolver_store
    with patch.object(app.state.sql_resolver, "_engine", mock_engine), patch.object(
        store, "ancestor_map", {}
    ), patch.object(store, "concepts_by_id", {}):
        response = client.post(
            "/concepts/search",
            json={"concept_id": [320128], "include_ancestors": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["children"] == []


def test_include_ancestors_true_sql_has_no_join_or_aggregate():
    rows = [_make_row(320128, "Essential hypertension", "Condition", 1000, 1, 50, cnt=1)]
    mock_engine, raw_conn = _mock_engine(rows)
    store = app.state.resolver_store
    with patch.object(app.state.sql_resolver, "_engine", mock_engine), patch.object(
        store, "ancestor_map", {}
    ), patch.object(store, "concepts_by_id", {}):
        response = client.post(
            "/concepts/search",
            json={"concept_id": [320128], "include_ancestors": True},
        )

    assert response.status_code == 200
    sql, _bindings = raw_conn.cursor.return_value.execute.call_args[0]
    assert "concept_ancestors" not in sql
    assert "JSON_ARRAYAGG" not in sql
    # Only the inner `base` CTE groups; no outer GROUP BY was needed for children.
    assert sql.count("GROUP BY") == 1


def test_parse_children_accepts_json_string_and_filters_nulls():
    from concepts import ConceptSearchResult

    result = ConceptSearchResult.model_validate(
        {
            "concept_id": 320128,
            "name": "Essential hypertension",
            "category": "Condition",
            "match_score": 1000,
            "collection_score": 0,
            "ncollections": 1,
            "count": 50,
            "children": '[{"concept_id": 99, "name": "Child", "category": "Condition"}, null]',
        }
    )
    assert len(result.children) == 1
    assert result.children[0].concept_id == 99


def test_parse_children_passes_list_through():
    from concepts import ConceptSearchResult

    result = ConceptSearchResult.model_validate(
        {
            "concept_id": 320128,
            "name": "Essential hypertension",
            "category": "Condition",
            "match_score": 1000,
            "collection_score": 0,
            "ncollections": 1,
            "count": 50,
            "children": [{"concept_id": 99, "name": "Child", "category": "Condition"}],
        }
    )
    assert len(result.children) == 1
    assert result.children[0].name == "Child"


def test_separator_variants_match_via_normalisation():
    """Non-alphanumeric separators are replaced with % in the LIKE pattern."""
    mock_engine, raw_conn = _mock_engine([])
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post(
            "/concepts/search",
            json={"concept_name": ["sickle cell-hemoglobin"], "include_ancestors": False},
        )

    assert response.status_code == 200
    call_args = raw_conn.cursor.return_value.execute.call_args
    _sql, bindings = call_args[0]
    # The LIKE binding should use % where the hyphen/space was
    like_bindings = [b for b in bindings if isinstance(b, str) and "%" in b and "sickle" in b.lower()]
    assert any("sickle%cell%hemoglobin" in b.lower() for b in like_bindings)


def test_empty_rows_returns_zero_total():
    mock_engine, _ = _mock_engine([])
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post("/concepts/search", json={"concept_name": ["xyz_no_match"], "include_ancestors": False})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["data"] == []
    assert body["last_page"] == 1


def _medcat_response(pretty_name: str, acc: float = 0.8) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "result": {
            "annotations": [
                {
                    "0": {
                        "pretty_name": pretty_name,
                        "acc": acc,
                        "meta_anns": {"Status": {"value": "Affirmed"}},
                    }
                }
            ]
        }
    }
    return mock_resp


def test_medcat_expansion_augments_search_terms(monkeypatch):
    """When a MedCATClient is configured, the pretty_name is added as an extra search term."""
    mock_engine, raw_conn = _mock_engine([])
    mock_client = MedCATClient("http://medcat.example.com", min_acc=0.5)
    with (
        patch.object(app.state.sql_resolver, "_engine", mock_engine),
        patch.object(app.state.sql_resolver, "_medcat_client", mock_client),
        patch("resolvers.medcat_client.httpx.post", return_value=_medcat_response("Chronic Kidney Diseases")),
    ):
        response = client.post(
            "/concepts/search",
            json={"concept_name": ["CKD"], "include_ancestors": False},
        )

    assert response.status_code == 200
    _sql, bindings = raw_conn.cursor.return_value.execute.call_args[0]
    str_bindings = [b for b in bindings if isinstance(b, str)]
    # Original term still present
    assert any("ckd" in b.lower() for b in str_bindings)
    # MedCAT expansion present with trailing 's' stripped (matches singular and plural)
    assert any("%chronic%kidney%disease%" == b.lower() for b in str_bindings)


def test_medcat_unavailable_falls_back_to_original(monkeypatch):
    """When the MedCAT call raises, the original term is still searched."""
    mock_engine, raw_conn = _mock_engine([])
    mock_client = MedCATClient("http://medcat.example.com")
    with (
        patch.object(app.state.sql_resolver, "_engine", mock_engine),
        patch.object(app.state.sql_resolver, "_medcat_client", mock_client),
        patch("resolvers.medcat_client.httpx.post", side_effect=ConnectionError("unreachable")),
    ):
        response = client.post(
            "/concepts/search",
            json={"concept_name": ["CKD"], "include_ancestors": False},
        )

    assert response.status_code == 200
    _sql, bindings = raw_conn.cursor.return_value.execute.call_args[0]
    str_bindings = [b for b in bindings if isinstance(b, str)]
    assert any("ckd" in b.lower() for b in str_bindings)


def test_medcat_url_not_set_does_not_crash(monkeypatch):
    """When no MedCATClient is configured the endpoint works without calling MedCAT."""
    mock_engine, raw_conn = _mock_engine([])
    with (
        patch.object(app.state.sql_resolver, "_engine", mock_engine),
        patch.object(app.state.sql_resolver, "_medcat_client", None),
        patch("resolvers.medcat_client.httpx.post", side_effect=Exception("should not be called")),
    ):
        response = client.post(
            "/concepts/search",
            json={"concept_name": ["CKD"], "include_ancestors": False},
        )

    assert response.status_code == 200
    _sql, bindings = raw_conn.cursor.return_value.execute.call_args[0]
    str_bindings = [b for b in bindings if isinstance(b, str)]
    assert any("ckd" in b.lower() for b in str_bindings)



def test_collection_filter_restricts_results():
    mock_engine, raw_conn = _mock_engine([_make_row(1, "Diabetes", "Condition", 500, 2, 100, cnt=1)])
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        response = client.post(
            "/concepts/search",
            json={
                "concept_name": ["diabetes"],
                "collection_ids": [3, 7],
                "use_collection_filter": True,
                "include_ancestors": False,
            },
        )

    assert response.status_code == 200
    sql, bindings = raw_conn.cursor.return_value.execute.call_args[0]
    assert "d.collection_id IN" in sql
    assert 3 in bindings
    assert 7 in bindings


def test_multiple_collection_ids_all_in_bindings():
    mock_engine, raw_conn = _mock_engine([])
    with patch.object(app.state.sql_resolver, "_engine", mock_engine):
        client.post(
            "/concepts/search",
            json={
                "collection_ids": [1, 2, 3],
                "use_collection_filter": True,
                "include_ancestors": False,
            },
        )

    _sql, bindings = raw_conn.cursor.return_value.execute.call_args[0]
    assert all(cid in bindings for cid in [1, 2, 3])
