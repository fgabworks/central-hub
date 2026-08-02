"""Focused tests for Live Data Export Phase 1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub.live_data_export.demo import ensure_export_demo_table
from hub.live_data_export.formats import write_csv, write_xlsx
from hub.live_data_export.query import build_select
from hub.live_data_export.registry import LiveExportRegistry, RegistryError, clear_registry_cache
from hub.live_data_export.security import ExportSafetyError, resolve_columns, validate_filters
from hub.live_data_export.service import LiveDataExportService
from hub.live_data_export.store import LiveExportStore
from hub.sql_workspace.connections import load_connection_registry
from hub.sql_workspace.demo import ensure_demo_database


@pytest.fixture()
def registry(tmp_path: Path) -> LiveExportRegistry:
    clear_registry_cache()
    # Use real project registry
    return LiveExportRegistry()


@pytest.fixture()
def service(tmp_path: Path) -> LiveDataExportService:
    clear_registry_cache()
    ensure_demo_database()
    ensure_export_demo_table()
    store = LiveExportStore(
        db_path=tmp_path / "lex.db",
        artifacts_root=tmp_path / "artifacts",
    )
    return LiveDataExportService(
        registry=LiveExportRegistry(),
        store=store,
        connections=load_connection_registry(),
    )


def test_registry_allowlisting(registry: LiveExportRegistry):
    demo = registry.require_available("demo_household_linelist", environment="dev")
    assert demo.available
    assert "household_id" in demo.allowed_columns
    with pytest.raises(RegistryError):
        registry.require_available("household_linelist", environment="live")
    with pytest.raises(RegistryError):
        registry.require_available("not_a_real_source", environment="live")


def test_blocked_arbitrary_source(service: LiveDataExportService):
    with pytest.raises(RegistryError):
        service.preview(
            source_key="pg_catalog.pg_user",
            filters={"environment": "dev", "quarter": "2025Q1"},
            columns=["household_id"],
            actor="test",
        )


def test_blocked_sensitive_and_excluded_columns(registry: LiveExportRegistry):
    src = registry.require_available("demo_household_linelist", environment="dev")
    with pytest.raises(ExportSafetyError):
        resolve_columns(src, ["internal_notes"])
    with pytest.raises(ExportSafetyError):
        resolve_columns(src, ["not_a_column"])


def test_required_filter_enforcement(registry: LiveExportRegistry):
    src = registry.require_available("demo_household_linelist", environment="dev")
    with pytest.raises(ExportSafetyError):
        validate_filters(src, {"environment": "dev"})
    ok = validate_filters(src, {"environment": "dev", "quarter": "2025Q1"})
    assert ok["quarter"] == "2025Q1"


def test_parameterized_quarter_and_ou_filters(registry: LiveExportRegistry):
    src = registry.require_available("demo_household_linelist", environment="dev")
    built = build_select(
        src,
        filters={
            "environment": "dev",
            "quarter": "2025Q1",
            "organisation_unit": {"uid": "OuRegion01"},
            "row_limit": 10,
        },
        columns=["household_id", "quarter"],
        dialect="sqlite",
    )
    assert ":quarter" in built.sql
    assert ":org_unit_uid" in built.sql
    assert built.params["quarter"] == "2025Q1"
    assert built.params["org_unit_uid"] == "OuRegion01"
    assert "JOIN" not in built.sql.upper()
    # Reject SQL-ish filter keys
    with pytest.raises(ExportSafetyError):
        validate_filters(src, {"environment": "dev", "quarter": "2025Q1", "sql": "DROP TABLE x"})


def test_preview_and_export_identical_scope(service: LiveDataExportService):
    filters = {"environment": "dev", "quarter": "2025Q1", "row_limit": 50}
    columns = ["household_id", "quarter", "status"]
    preview = service.preview(
        source_key="demo_household_linelist",
        filters=filters,
        columns=columns,
        actor="test",
    )
    assert preview["ok"]
    assert preview["estimated_rows"] >= 1
    assert preview["columns"] == columns
    result = service.export(
        source_key="demo_household_linelist",
        filters=filters,
        columns=columns,
        format="csv",
        actor="test",
    )
    assert result["ok"]
    job = result["job"]
    assert job["status"] == "ready"
    assert job["exported_rows"] == preview["estimated_rows"] or job["exported_rows"] <= filters["row_limit"]
    assert job["filters"]["quarter"] == "2025Q1"
    assert job["columns"] == columns


def test_row_limit_enforcement(service: LiveDataExportService):
    result = service.export(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q1", "row_limit": 2},
        columns=["household_id"],
        format="csv",
        actor="test",
    )
    assert result["job"]["exported_rows"] == 2


def test_csv_and_xlsx_generation(tmp_path: Path, service: LiveDataExportService):
    cols = ["a", "b"]
    rows = [["1", "2"], ["3", "4"]]
    csv_path = tmp_path / "t.csv"
    xlsx_path = tmp_path / "t.xlsx"
    assert write_csv(csv_path, cols, rows) > 0
    assert write_xlsx(xlsx_path, cols, rows) > 0
    csv_job = service.export(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q2"},
        columns=["household_id", "org_unit_name"],
        format="csv",
        actor="test",
    )
    xlsx_job = service.export(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q2"},
        columns=["household_id", "org_unit_name"],
        format="xlsx",
        actor="test",
    )
    assert csv_job["job"]["status"] == "ready"
    assert xlsx_job["job"]["status"] == "ready"
    path, name, _ = service.resolve_download(
        csv_job["job"]["id"],
        token=csv_job["job"]["download_token"],
        actor="test",
    )
    assert path.is_file()
    assert name.endswith(".csv")


def test_large_export_job_flow(service: LiveDataExportService):
    # Force async path
    result = service.export(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q1"},
        columns=["household_id"],
        format="csv",
        actor="test",
        force_async=True,
    )
    assert result["mode"] == "async"
    job_id = result["job"]["id"]
    # Run synchronously for test determinism
    done = service._run_job(job_id)
    assert done["status"] in {"ready", "cancelled"}


def test_duplicate_job_prevention(service: LiveDataExportService):
    filters = {"environment": "dev", "quarter": "2025Q3", "row_limit": 10}
    columns = ["household_id"]
    # Create an active job without finishing
    first = service.store.create_job(
        source_key="demo_household_linelist",
        environment="dev",
        format="csv",
        filters=filters,
        columns=columns,
        actor="test",
        fingerprint=service._fingerprint(
            "demo_household_linelist", "dev", "csv", filters, columns
        ),
    )
    assert first["status"] == "queued"
    with pytest.raises(ExportSafetyError, match="Duplicate"):
        service.export(
            source_key="demo_household_linelist",
            filters=filters,
            columns=columns,
            format="csv",
            actor="test",
        )


def test_cancellation(service: LiveDataExportService):
    job = service.store.create_job(
        source_key="demo_household_linelist",
        environment="dev",
        format="csv",
        filters={"environment": "dev", "quarter": "2025Q1"},
        columns=["household_id"],
        actor="test",
        fingerprint="cancel-test",
    )
    cancelled = service.cancel(job["id"], actor="test")
    assert cancelled["status"] == "cancelled"


def test_expired_download_rejection(service: LiveDataExportService):
    result = service.export(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q1"},
        columns=["household_id"],
        format="csv",
        actor="test",
    )
    job = result["job"]
    service.store.update_job(job["id"], expires_at="2000-01-01T00:00:00Z", status="ready")
    with pytest.raises(ExportSafetyError, match="expired"):
        service.resolve_download(job["id"], token=job["download_token"], actor="test")


def test_audit_history_created(service: LiveDataExportService):
    service.preview(
        source_key="demo_household_linelist",
        filters={"environment": "dev", "quarter": "2025Q1"},
        columns=["household_id"],
        actor="tester",
    )
    hist = service.store.list_history()
    assert any(h["event"] == "preview" and h["actor"] == "tester" for h in hist)
    # No row payloads in history
    for h in hist:
        blob = json.dumps(h.get("detail") or {})
        assert "HH-001" not in blob


def test_stage_live_isolation(service: LiveDataExportService):
    src = service.registry.require_available("demo_household_linelist", environment="live")
    # Demo pins local-demo — live-ro must not be selected for this source
    profile = service._resolve_profile(src, "live")
    assert profile.id == "local-demo"
    # Cross-wire blocked when connection is stage-ro for live env
    from hub.live_data_export.registry import ExportSource

    fake = ExportSource(
        source_key="x",
        display_name="x",
        source_type="table",
        status="verified",
        enabled=True,
        description="",
        source_owner="",
        repository="",
        connection_id="stage-ro",
        schema="public",
        object_name="t",
        saved_query_id="",
        allowed_columns=["a"],
        default_columns=["a"],
        sensitive_columns=[],
        excluded_columns=[],
        required_filters=[],
        filters_supported=[],
        quarter_column="",
        organisation_unit_column="",
        date_column="",
        status_column="",
        ip_column="",
        maximum_rows=10,
        supported_formats=["csv"],
        default_sort=[],
        enabled_environments=["live"],
    )
    with pytest.raises(ExportSafetyError, match="Stage connection cannot"):
        service._resolve_profile(fake, "live")


def test_no_database_writes_in_built_sql(registry: LiveExportRegistry):
    src = registry.require_available("demo_household_linelist", environment="dev")
    built = build_select(
        src,
        filters={"environment": "dev", "quarter": "2025Q1"},
        columns=["household_id"],
        dialect="sqlite",
    )
    upper = built.sql.upper()
    for banned in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CALL"):
        assert banned not in upper
    assert upper.strip().startswith("SELECT")
