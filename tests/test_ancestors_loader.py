from unittest.mock import MagicMock, patch

from loaders.ancestors import load_ancestor_map

DB_CONFIG = {"host": "localhost", "user": "u", "password": "p", "database": "omop"}


def _mock_conn(table_exists=True, rows=None):
    """Build a mysql.connector connection mock. conn.cursor() returns the same cursor
    for both the SHOW TABLES probe and the SELECT (fetchone/fetchall serve each)."""
    cursor = MagicMock()
    cursor.fetchone.return_value = ("concept_ancestors",) if table_exists else None
    cursor.fetchall.return_value = rows or []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_empty_ids_returns_empty_without_connecting():
    with patch("mysql.connector.connect") as connect:
        assert load_ancestor_map(DB_CONFIG, []) == {}
        connect.assert_not_called()


def test_table_absent_returns_empty():
    conn, _ = _mock_conn(table_exists=False)
    with patch("mysql.connector.connect", return_value=conn):
        assert load_ancestor_map(DB_CONFIG, [1, 2]) == {}


def test_row_accumulation_and_dedup():
    rows = [
        {"parent_concept_id": 1, "child_concept_id": 2},
        {"parent_concept_id": 1, "child_concept_id": 3},
        {"parent_concept_id": 4, "child_concept_id": 5},
        {"parent_concept_id": 1, "child_concept_id": 2},  # duplicate edge
    ]
    conn, _ = _mock_conn(rows=rows)
    with patch("mysql.connector.connect", return_value=conn):
        result = load_ancestor_map(DB_CONFIG, [1, 2, 3, 4, 5])
    assert result == {1: [2, 3], 4: [5]}


def test_bindings_bound_on_both_sides_and_excludes_self():
    conn, cursor = _mock_conn(rows=[])
    with patch("mysql.connector.connect", return_value=conn):
        load_ancestor_map(DB_CONFIG, [10, 20])

    # The second execute is the SELECT (the first is the SHOW TABLES probe).
    sql, bindings = cursor.execute.call_args_list[-1][0]
    assert sql.count("IN (") == 2
    assert "parent_concept_id != child_concept_id" in sql
    assert bindings == [10, 20, 10, 20]


def test_exception_returns_empty():
    with patch("mysql.connector.connect", side_effect=Exception("boom")):
        assert load_ancestor_map(DB_CONFIG, [1]) == {}
