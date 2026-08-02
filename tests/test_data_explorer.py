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
    with pytest.raises(ExplorerSafetyError, match="not configured|missing|Connection"):
        service._resolve_profile("live")
    # Cross-wire blocked at mapping level
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
