"""Focused tests for Data Explorer Phase 1."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.data_explorer.browse import build_browse_query
from hub.data_explorer.classifier import build_inventory, classify_object
from hub.data_explorer.config import clear_explorer_config_cache, load_explorer_config
from hub.data_explorer.discovery import ObjectMeta, ColumnMeta, discover_catalog, invalidate_catalog_cache
from hub.data_explorer.lineage import build_lineage_index
from hub.data_explorer.security import ExplorerSafetyError, apply_column_policies, assert_safe_identifier
from hub.data_explorer.service import DataExplorerService
from hub.data_explorer.store import ExplorerStore
from hub.live_data_export.demo import ensure_export_demo_table
from hub.sql_workspace.connections import load_connection_registry
from hub.sql_workspace.demo import ensure_demo_database
from hub.sql_workspace.safety import validate_readonly_sql


@pytest.fixture()
def service(tmp_path: Path) -> DataExplorerService:
    clear_explorer_config_cache()
    invalidate_catalog_cache()
    ensure_demo_database()
    ensure_export_demo_table()
    return DataExplorerService(
        connections=load_connection_registry(),
        store=ExplorerStore(db_path=tmp_path / "dex.db"),
        config=load_explorer_config(),
    )


def test_metadata_discovery(service: DataExplorerService):
    catalog = service.catalog(environment="dev", force=True, actor="test")
    names = {o.name for o in catalog.objects}
    assert "demo_people" in names or "export_demo_household" in names
    assert "pg_catalog" not in catalog.schemas
    assert "information_schema" not in catalog.schemas


def test_schema_table_view_tree(service: DataExplorerService):
    tree = service.tree(environment="dev", actor="test")
    assert tree["schemas"]
    assert any(s["tables"] or s["views"] for s in tree["schemas"])


def test_system_schema_exclusion():
    from hub.data_explorer.security import is_excluded_schema

    assert is_excluded_schema("pg_catalog")
    assert is_excluded_schema("information_schema")
    assert not is_excluded_schema("public")


def test_read_only_enforcement(service: DataExplorerService):
    with pytest.raises(Exception):
        validate_readonly_sql("DELETE FROM demo_people", dialect="sqlite")
    with pytest.raises(Exception):
        validate_readonly_sql("INSERT INTO demo_people VALUES (1,'x','y',1)", dialect="sqlite")


def test_identifier_validation():
    assert assert_safe_identifier("household_id") == "household_id"
    with pytest.raises(ExplorerSafetyError):
        assert_safe_identifier("household;drop")
    with pytest.raises(ExplorerSafetyError):
        assert_safe_identifier("pg_catalog")


def test_pagination_and_filters(service: DataExplorerService):
    result = service.browse(
        environment="dev",
        schema="main",
        name="export_demo_household",
        filters=[{"column": "quarter", "op": "eq", "value": "2025Q1"}],
        page=1,
        page_size=2,
        actor="test",
    )
    assert result["ok"]
    assert result["page_size"] == 2
    assert len(result["rows"]) <= 2
    assert result["total_rows"] >= 1
    assert ":quarter" in result["safe_query"] or "quarter" in result["safe_query"].lower()


def test_multiple_filters_use_server_side_and_logic(service: DataExplorerService):
    result = service.browse(
        environment="dev",
        schema="main",
        name="export_demo_household",
        filters=[
            {"column": "quarter", "op": "eq", "value": "2025Q1"},
            {"column": "status", "op": "eq", "value": "Active"},
        ],
        page=1,
        page_size=100,
        actor="test",
    )
    assert result["total_rows"] == 2
    assert result["filtered_rows"] == 2
    assert len(result["rows"]) == 2
    assert " AND " in result["safe_query"]
    assert "2025Q1" not in result["safe_query"]
    assert "Active" not in result["safe_query"]


def test_server_side_sorting_and_reset_query_shape(service: DataExplorerService):
    descending = service.browse(
        environment="dev",
        schema="main",
        name="export_demo_household",
        sort_column="household_id",
        sort_dir="desc",
        page=1,
        page_size=2,
        actor="test",
    )
    assert [row[0] for row in descending["rows"]] == ["HH-008", "HH-007"]
    assert 'ORDER BY "household_id" DESC' in descending["safe_query"]

    obj = service._require_object("dev", "main", "export_demo_household")
    reset = build_browse_query(obj, sort_column=None, limit=2, dialect="sqlite")
    assert "ORDER BY" not in reset.sql
    with pytest.raises(ExplorerSafetyError, match="direction"):
        build_browse_query(
            obj,
            sort_column="household_id",
            sort_dir="sideways",
            limit=2,
            dialect="sqlite",
        )


def test_filter_operators_are_type_checked_and_hidden_columns_are_rejected(
    service: DataExplorerService,
):
    with pytest.raises(ExplorerSafetyError, match="not valid"):
        service.browse(
            environment="dev",
            schema="main",
            name="demo_people",
            filters=[{"column": "id", "op": "contains", "value": "1"}],
            actor="test",
        )
    with pytest.raises(ExplorerSafetyError, match="Numeric"):
        service.browse(
            environment="dev",
            schema="main",
            name="demo_people",
            filters=[{"column": "id", "op": "eq", "value": "not-a-number"}],
            actor="test",
        )
    with pytest.raises(ExplorerSafetyError, match="not valid"):
        service.browse(
            environment="dev",
            schema="main",
            name="demo_people",
            filters=[{"column": "name", "op": "gt", "value": "Ada"}],
            actor="test",
        )
    hidden = ObjectMeta(
        schema="main",
        name="sensitive_demo",
        object_type="table",
        columns=[
            ColumnMeta(name="id", data_type="INTEGER", nullable=False),
            ColumnMeta(name="password_hash", data_type="TEXT", nullable=True),
        ],
    )
    with pytest.raises(ExplorerSafetyError, match="browsable"):
        build_browse_query(
            hidden,
            filters=[{"column": "password_hash", "op": "eq", "value": "secret"}],
            dialect="sqlite",
        )


def test_object_detail_exposes_only_type_valid_filter_operators(service: DataExplorerService):
    detail = service.object_detail(
        environment="dev", schema="main", name="demo_people", actor="test"
    )
    columns = {column["name"]: column for column in detail["object"]["columns"]}
    assert "contains" in columns["name"]["filter_operators"]
    assert "gt" not in columns["name"]["filter_operators"]
    assert "gt" in columns["id"]["filter_operators"]
    assert "contains" not in columns["id"]["filter_operators"]


def test_large_table_safeguards(service: DataExplorerService):
    result = service.browse(
        environment="dev",
        schema="main",
        name="export_demo_household",
        page_size=99999,
        actor="test",
    )
    assert result["page_size"] <= service.config.defaults.max_page_size
    # Export hard-capped
    exported = service.export(
        environment="dev",
        schema="main",
        name="export_demo_household",
        columns=["household_id"],
        filters=None,
        format="csv",
        actor="test",
        row_limit=10_000_000,
    )
    assert exported["exported_rows"] <= service.config.defaults.max_export_rows
    assert exported["exported_rows"] <= 8  # demo table size


def test_column_masking():
    cols, warnings, actions = apply_column_policies(
        ["household_id", "password_hash", "email"]
    )
    assert "password_hash" not in cols
    assert "email" in cols
    assert actions.get("email") == "mask"
    assert any("Hidden" in w for w in warnings)


def test_report_indicator_lineage_mapping(service: DataExplorerService):
    catalog = service.catalog(environment="dev", force=True)
    idx = build_lineage_index(catalog.objects)
    # Demo export source should map to export_demo_household
    matched = False
    for key, payload in idx.items():
        if key == "__unresolved__":
            continue
        if payload.get("exports"):
            matched = True
            assert payload["exports"][0]["confidence"] == "verified"
    assert matched
    # HCSC analytics refs should be unresolved (not invented as tables)
    unr = idx.get("__unresolved__", {})
    assert unr.get("indicators")
    assert all(i.get("unresolved_mapping") for i in unr["indicators"])


def test_csv_xlsx_export(service: DataExplorerService, tmp_path: Path):
    csv_res = service.export(
        environment="dev",
        schema="main",
        name="export_demo_household",
        columns=["household_id", "quarter"],
        filters=[{"column": "quarter", "op": "eq", "value": "2025Q1"}],
        format="csv",
        actor="test",
        row_limit=10,
    )
    assert csv_res["ok"]
    assert Path(csv_res["path"]).is_file()
    xlsx_res = service.export(
        environment="dev",
        schema="main",
        name="export_demo_household",
        columns=["household_id"],
        filters=None,
        format="xlsx",
        actor="test",
        row_limit=10,
    )
    assert xlsx_res["ok"]
    assert Path(xlsx_res["path"]).is_file()


def test_audit_logging(service: DataExplorerService):
    service.browse(
        environment="dev",
        schema="main",
        name="demo_people",
        actor="auditor",
    )
    rows = service.store.list_audit()
    assert any(r["event"] == "browse" and r["actor"] == "auditor" for r in rows)
    for r in rows:
        blob = str(r.get("detail"))
        assert "Ada" not in blob  # no row contents


def test_stage_live_isolation(service: DataExplorerService):
    # Cross-wire blocked at mapping level regardless of whether Live is configured.
    from hub.data_explorer.config import ExplorerDefaults, ExplorerConfig

    cfg = load_explorer_config()
    # force bad mapping
    service.config.defaults.connection_by_environment["live"] = "stage-ro"
    with pytest.raises(ExplorerSafetyError, match="Stage connection cannot"):
        service._resolve_profile("live")
    service.config.defaults.connection_by_environment["live"] = "live-ro"


def test_no_database_writes_in_browse_sql():
    obj = ObjectMeta(
        schema="main",
        name="export_demo_household",
        object_type="table",
        columns=[
            ColumnMeta(name="household_id", data_type="TEXT", nullable=False),
            ColumnMeta(name="quarter", data_type="TEXT", nullable=False),
        ],
    )
    q = build_browse_query(obj, columns=["household_id"], dialect="sqlite", limit=5)
    upper = q.sql.upper()
    assert upper.strip().startswith("SELECT")
    for banned in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
        assert banned not in upper


def test_inventory_groups(service: DataExplorerService):
    inv = service.inventory(environment="dev", actor="test")
    assert "groups" in inv
    assert "Application/Internal" in inv["groups"]
    assert inv["object_count"] >= 1
    # demo tables classified as Application/Internal via pattern
    app = inv["groups"]["Application/Internal"]
    assert any("demo" in (x["object_name"] or "") for x in app)


def test_classify_unknown():
    obj = ObjectMeta(schema="public", name="zz_mystery_xyz", object_type="table")
    cls = classify_object(obj)
    assert cls["group"] == "Unknown"
    assert cls["confidence"] == "unresolved"


def test_blocked_arbitrary_object(service: DataExplorerService):
    with pytest.raises(ExplorerSafetyError):
        service.browse(
            environment="dev",
            schema="main",
            name="not_a_real_table_zzz",
            actor="test",
        )


def test_connection_failure_is_safe_explorer_error(service: DataExplorerService, monkeypatch):
    def fail_discovery(*args, **kwargs):
        raise RuntimeError("password=must-not-leak host=private-db")

    monkeypatch.setattr("hub.data_explorer.service.discover_catalog", fail_discovery)
    with pytest.raises(ExplorerSafetyError) as caught:
        service.catalog(environment="dev", force=True, actor="test")
    message = str(caught.value)
    assert "Unable to connect to the Dev read-only database" in message
    assert "must-not-leak" not in message
    assert "private-db" not in message


def test_unified_navigation_redirect_and_export_api(tmp_path: Path):
    from app import create_app

    app = create_app()
    unified = DataExplorerService(
        connections=load_connection_registry(),
        store=ExplorerStore(
            db_path=tmp_path / "unified.db",
            artifacts_root=tmp_path / "exports",
        ),
        config=load_explorer_config(),
    )
    app.config["DATA_EXPLORER"] = unified
    app.config["LIVE_DATA_EXPORT"] = unified.exports
    client = app.test_client()

    legacy = client.get("/live-data-export")
    assert legacy.status_code == 302
    assert legacy.headers["Location"].endswith("/data-explorer?tab=export")

    page = client.get("/data-explorer?tab=export")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert ">Live Data Export<" not in html
    for label in (
        "Browse Data",
        "Schema",
        "Relationships",
        "Lineage",
        "Export",
        "Export Jobs",
        "History",
    ):
        assert f'>{label}<' in html
    assert app.config["LIVE_DATA_EXPORT"] is app.config["DATA_EXPLORER"].exports

    preview = client.post(
        "/api/data-explorer/exports/preview",
        json={
            "source_key": "demo_household_linelist",
            "filters": {"environment": "dev", "quarter": "2025Q1", "row_limit": 2},
            "columns": ["household_id", "quarter"],
        },
    )
    assert preview.status_code == 200
    assert preview.get_json()["estimated_rows"] >= 1

    generated = client.post(
        "/api/data-explorer/exports",
        json={
            "source_key": "demo_household_linelist",
            "filters": {"environment": "dev", "quarter": "2025Q1", "row_limit": 2},
            "columns": ["household_id", "quarter"],
            "format": "csv",
        },
    )
    assert generated.status_code == 200
    payload = generated.get_json()
    assert payload["job"]["status"] == "ready"
    assert client.get("/api/data-explorer/export-jobs").status_code == 200
    assert client.get("/api/data-explorer/export-history").status_code == 200


def test_unified_export_api_rejects_arbitrary_table_input(tmp_path: Path):
    from app import create_app

    app = create_app()
    unified = DataExplorerService(
        connections=load_connection_registry(),
        store=ExplorerStore(db_path=tmp_path / "safety.db"),
        config=load_explorer_config(),
    )
    app.config["DATA_EXPLORER"] = unified
    app.config["LIVE_DATA_EXPORT"] = unified.exports
    response = app.test_client().post(
        "/api/data-explorer/exports/preview",
        json={
            "source_key": "demo_household_linelist",
            "filters": {
                "environment": "dev",
                "quarter": "2025Q1",
                "table": "pg_catalog.pg_user",
            },
        },
    )
    assert response.status_code == 400
    assert "not allowed" in response.get_json()["error"]
