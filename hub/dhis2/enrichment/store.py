"""High-level enrichment store operations."""

from __future__ import annotations

import json
import uuid
from typing import Any

from hub.dhis2.enrichment.db import EnrichmentDatabase, utcnow


class EnrichmentStore:
    def __init__(self, db: EnrichmentDatabase | None = None) -> None:
        self.db = db or EnrichmentDatabase()

    def current_snapshot_id(self) -> str | None:
        row = self.db.fetchone(
            "SELECT id FROM enrichment_snapshots WHERE is_current = 1 ORDER BY created_at DESC LIMIT 1"
        )
        return str(row["id"]) if row else None

    def checksum_map(self, *, snapshot_id: str | None = None) -> dict[str, str]:
        """UID → checksum for a snapshot (defaults to current)."""
        snap = snapshot_id or self.current_snapshot_id()
        if not snap:
            return {}
        rows = self.db.fetchall(
            """
            SELECT uid, checksum FROM metadata_objects
            WHERE snapshot_id = ? AND checksum IS NOT NULL AND checksum != ''
            """,
            (snap,),
        )
        return {str(r["uid"]): str(r["checksum"]) for r in rows}

    def list_snapshots(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.db.fetchall(
            "SELECT * FROM enrichment_snapshots ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        )

    def get_object(self, uid: str, *, snapshot_id: str | None = None) -> dict[str, Any] | None:
        snap = snapshot_id or self.current_snapshot_id()
        if not snap:
            return None
        row = self.db.fetchone(
            "SELECT * FROM metadata_objects WHERE snapshot_id = ? AND uid = ?",
            (snap, uid),
        )
        if not row:
            return None
        return self._hydrate_object(row, snap)

    def relationships_for(
        self, uid: str, *, snapshot_id: str | None = None
    ) -> list[dict[str, Any]]:
        snap = snapshot_id or self.current_snapshot_id()
        if not snap:
            return []
        rows = self.db.fetchall(
            """
            SELECT * FROM metadata_relationships
            WHERE snapshot_id = ? AND (from_uid = ? OR to_uid = ?)
            ORDER BY rel_type, to_name, to_uid
            """,
            (snap, uid, uid),
        )
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.get("detail_json") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            out.append(item)
        return out

    def option_set_options(
        self, option_set_uid: str, *, snapshot_id: str | None = None
    ) -> list[dict[str, Any]]:
        snap = snapshot_id or self.current_snapshot_id()
        if not snap:
            return []
        return self.db.fetchall(
            """
            SELECT * FROM option_set_options
            WHERE snapshot_id = ? AND option_set_uid = ?
            ORDER BY sort_order, name
            """,
            (snap, option_set_uid),
        )

    def search(
        self,
        *,
        object_type: str = "",
        program: str = "",
        program_stage: str = "",
        domain_type: str = "",
        value_type: str = "",
        answer_type: str = "",
        option_set: str = "",
        audit_status: str = "",
        environment: str = "",
        q: str = "",
        limit: int | None = 200,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        snap = self.current_snapshot_id()
        if not snap:
            return [], 0

        clauses = ["snapshot_id = ?"]
        params: list[Any] = [snap]
        if object_type:
            clauses.append("object_type = ?")
            params.append(object_type)
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        if domain_type:
            clauses.append("domain_type = ?")
            params.append(domain_type)
        if value_type:
            clauses.append("value_type = ?")
            params.append(value_type)
        if answer_type:
            clauses.append("answer_type = ?")
            params.append(answer_type)
        if option_set:
            clauses.append("option_set_uid = ?")
            params.append(option_set)
        if program:
            clauses.append(
                """uid IN (
                    SELECT from_uid FROM metadata_relationships
                    WHERE snapshot_id = ? AND rel_type IN (
                        'DATA_ELEMENT_IN_PROGRAM_STAGE','PROGRAM_INDICATOR_BELONGS_TO_PROGRAM','ATTRIBUTE_IN_PROGRAM'
                    ) AND (to_uid = ? OR detail_json LIKE ?)
                ) OR program_uid = ?"""
            )
            params.extend([snap, program, f'%"{program}"%', program])
        if program_stage:
            clauses.append(
                """uid IN (
                    SELECT from_uid FROM metadata_relationships
                    WHERE snapshot_id = ? AND rel_type = 'DATA_ELEMENT_IN_PROGRAM_STAGE'
                      AND to_uid = ?
                )"""
            )
            params.extend([snap, program_stage])
        if audit_status:
            clauses.append("audit_statuses LIKE ?")
            params.append(f"%{audit_status}%")
        if q:
            clauses.append("(uid LIKE ? OR name LIKE ? OR code LIKE ? OR form_name LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like, like, like])

        where = " AND ".join(clauses)
        total_row = self.db.fetchone(
            f"SELECT COUNT(*) AS n FROM metadata_objects WHERE {where}", tuple(params)
        )
        total = int((total_row or {}).get("n") or 0)

        sql = f"""
            SELECT uid, object_type, name, code, domain_type, value_type, answer_type,
                   option_set_uid, option_set_name, program_uid, program_name,
                   audit_statuses, fetched_at, checksum
            FROM metadata_objects
            WHERE {where}
            ORDER BY object_type, name, uid
        """
        if limit is None:
            rows = self.db.fetchall(sql, tuple(params))
        else:
            page = max(1, min(int(limit), 5000))
            off = max(0, int(offset))
            rows = self.db.fetchall(sql + " LIMIT ? OFFSET ?", tuple(params) + (page, off))
        for row in rows:
            try:
                row["audit_status_list"] = json.loads(row.get("audit_statuses") or "[]")
            except json.JSONDecodeError:
                row["audit_status_list"] = []
        return rows, total

    def facets(self) -> dict[str, list[str]]:
        snap = self.current_snapshot_id()
        if not snap:
            return {
                "object_types": [],
                "environments": [],
                "domain_types": [],
                "value_types": [],
                "answer_types": [],
                "programs": [],
                "program_stages": [],
                "option_sets": [],
                "audit_statuses": [],
            }

        def _col(column: str) -> list[str]:
            rows = self.db.fetchall(
                f"""
                SELECT DISTINCT {column} AS v FROM metadata_objects
                WHERE snapshot_id = ? AND {column} IS NOT NULL AND {column} != ''
                ORDER BY v
                """,
                (snap,),
            )
            return [str(r["v"]) for r in rows]

        stages = self.db.fetchall(
            """
            SELECT DISTINCT to_uid AS uid, to_name AS name
            FROM metadata_relationships
            WHERE snapshot_id = ? AND rel_type = 'DATA_ELEMENT_IN_PROGRAM_STAGE'
            ORDER BY to_name, to_uid
            """,
            (snap,),
        )
        programs = self.db.fetchall(
            """
            SELECT DISTINCT to_uid AS uid, to_name AS name
            FROM metadata_relationships
            WHERE snapshot_id = ? AND rel_type IN (
                'DATA_ELEMENT_IN_PROGRAM_STAGE','PROGRAM_INDICATOR_BELONGS_TO_PROGRAM','ATTRIBUTE_IN_PROGRAM'
            ) AND to_type = 'program'
            ORDER BY to_name, to_uid
            """,
            (snap,),
        )
        # Also programs nested on stage edges (detail may hold program)
        option_sets = self.db.fetchall(
            """
            SELECT DISTINCT option_set_uid AS uid, option_set_name AS name
            FROM metadata_objects
            WHERE snapshot_id = ? AND option_set_uid IS NOT NULL AND option_set_uid != ''
            ORDER BY option_set_name, option_set_uid
            """,
            (snap,),
        )
        audit_rows = self.db.fetchall(
            "SELECT audit_statuses FROM metadata_objects WHERE snapshot_id = ?",
            (snap,),
        )
        audits: set[str] = set()
        for row in audit_rows:
            try:
                audits.update(json.loads(row.get("audit_statuses") or "[]"))
            except json.JSONDecodeError:
                continue

        return {
            "object_types": _col("object_type"),
            "environments": _col("environment"),
            "domain_types": _col("domain_type"),
            "value_types": _col("value_type"),
            "answer_types": _col("answer_type"),
            "programs": [f"{p['uid']}|{p['name'] or p['uid']}" for p in programs],
            "program_stages": [f"{s['uid']}|{s['name'] or s['uid']}" for s in stages],
            "option_sets": [f"{o['uid']}|{o['name'] or o['uid']}" for o in option_sets],
            "audit_statuses": sorted(audits),
        }

    def save_snapshot(
        self,
        *,
        environment: str,
        objects: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        options: list[dict[str, Any]],
        stats: dict[str, Any] | None = None,
        notes: str = "",
    ) -> str:
        snap_id = datetime_stamp_id()
        created = utcnow()
        with self.db.connect() as conn:
            conn.execute("UPDATE enrichment_snapshots SET is_current = 0 WHERE is_current = 1")
            conn.execute(
                """
                INSERT INTO enrichment_snapshots
                (id, created_at, environment, is_current, object_count, relationship_count, stats_json, notes)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    snap_id,
                    created,
                    environment,
                    len(objects),
                    len(relationships),
                    json.dumps(stats or {}, ensure_ascii=True),
                    notes,
                ),
            )
            for obj in objects:
                conn.execute(
                    """
                    INSERT INTO metadata_objects (
                        uid, object_type, environment, name, short_name, code, description,
                        form_name, domain_type, value_type, aggregation_type, answer_type,
                        zero_is_significant, option_set_value, option_set_uid, option_set_name,
                        category_combo_uid, category_combo_name, analytics_type, decimals,
                        expression, filter, program_uid, program_name, checksum, fetched_at,
                        audit_statuses, summary_json, raw_json, snapshot_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        obj["uid"],
                        obj.get("object_type") or "",
                        environment,
                        obj.get("name"),
                        obj.get("short_name"),
                        obj.get("code"),
                        obj.get("description"),
                        obj.get("form_name"),
                        obj.get("domain_type"),
                        obj.get("value_type"),
                        obj.get("aggregation_type"),
                        obj.get("answer_type"),
                        1 if obj.get("zero_is_significant") else 0 if obj.get("zero_is_significant") is not None else None,
                        1 if obj.get("option_set_value") else 0 if obj.get("option_set_value") is not None else None,
                        obj.get("option_set_uid"),
                        obj.get("option_set_name"),
                        obj.get("category_combo_uid"),
                        obj.get("category_combo_name"),
                        obj.get("analytics_type"),
                        str(obj.get("decimals")) if obj.get("decimals") is not None else None,
                        obj.get("expression"),
                        obj.get("filter"),
                        obj.get("program_uid"),
                        obj.get("program_name"),
                        obj.get("checksum"),
                        obj.get("fetched_at"),
                        json.dumps(obj.get("audit_statuses") or [], ensure_ascii=True),
                        json.dumps(obj.get("summary") or {}, ensure_ascii=True),
                        # Raw only stored when explicitly provided (lazy for large sets)
                        obj.get("raw_json"),
                        snap_id,
                    ),
                )
            for rel in relationships:
                conn.execute(
                    """
                    INSERT INTO metadata_relationships
                    (snapshot_id, rel_type, from_uid, from_type, to_uid, to_type, to_name, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snap_id,
                        rel["rel_type"],
                        rel["from_uid"],
                        rel["from_type"],
                        rel["to_uid"],
                        rel["to_type"],
                        rel.get("to_name") or "",
                        json.dumps(rel.get("detail") or {}, ensure_ascii=True),
                    ),
                )
            for opt in options:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO option_set_options
                    (snapshot_id, option_set_uid, option_uid, name, code, sort_order, color, icon)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snap_id,
                        opt["option_set_uid"],
                        opt["option_uid"],
                        opt.get("name"),
                        opt.get("code"),
                        int(opt.get("sort_order") or 0),
                        opt.get("color"),
                        opt.get("icon"),
                    ),
                )
        return snap_id

    def create_run(self, *, environment: str) -> str:
        run_id = str(uuid.uuid4())
        now = utcnow()
        self.db.execute(
            """
            INSERT INTO enrichment_runs
            (id, status, phase, message, percent, cancel_requested, environment, created_at, updated_at)
            VALUES (?, 'queued', 'queued', 'Queued', 0, 0, ?, ?, ?)
            """,
            (run_id, environment, now, now),
        )
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(
            f"UPDATE enrichment_runs SET {cols} WHERE id = ?",
            tuple(fields.values()) + (run_id,),
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.db.fetchone("SELECT * FROM enrichment_runs WHERE id = ?", (run_id,))

    def request_cancel(self, run_id: str) -> None:
        self.update_run(run_id, cancel_requested=1, message="Cancel requested")

    def is_cancel_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        return bool(row and row.get("cancel_requested"))

    def _hydrate_object(self, row: dict[str, Any], snap: str) -> dict[str, Any]:
        item = dict(row)
        try:
            item["audit_status_list"] = json.loads(item.get("audit_statuses") or "[]")
        except json.JSONDecodeError:
            item["audit_status_list"] = []
        try:
            item["summary"] = json.loads(item.get("summary_json") or "{}")
        except json.JSONDecodeError:
            item["summary"] = {}
        item["relationships"] = self.relationships_for(item["uid"], snapshot_id=snap)
        if item.get("option_set_uid"):
            item["options"] = self.option_set_options(item["option_set_uid"], snapshot_id=snap)
        else:
            item["options"] = []
        return item


def datetime_stamp_id() -> str:
    return utcnow().replace(":", "").replace("-", "").replace(".", "")[:17] + "-" + uuid.uuid4().hex[:8]
