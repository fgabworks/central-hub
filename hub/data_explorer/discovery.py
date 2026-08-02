"""Read-only database metadata discovery (Postgres + SQLite)."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hub.data_explorer.config import ExplorerConfig, get_explorer_config
from hub.data_explorer.security import ExplorerSafetyError, is_excluded_schema
from hub.sql_workspace.connections import SqlConnectionProfile

log = logging.getLogger("hub.data_explorer.discovery")


@dataclass
class ColumnMeta:
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    ordinal: int = 0


@dataclass
class KeyMeta:
    name: str
    kind: str  # primary | foreign | unique
    columns: list[str]
    referenced_schema: str | None = None
    referenced_table: str | None = None
    referenced_columns: list[str] = field(default_factory=list)


@dataclass
class IndexMeta:
    name: str
    columns: list[str]
    unique: bool = False


@dataclass
class ObjectMeta:
    schema: str
    name: str
    object_type: str  # table | view | materialized_view
    columns: list[ColumnMeta] = field(default_factory=list)
    keys: list[KeyMeta] = field(default_factory=list)
    indexes: list[IndexMeta] = field(default_factory=list)
    estimated_rows: int | None = None
    size_bytes: int | None = None
    last_analyzed: str | None = None
    approximate: bool = False

    @property
    def full_name(self) -> str:
        if self.schema:
            return f"{self.schema}.{self.name}"
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "name": self.name,
            "full_name": self.full_name,
            "object_type": self.object_type,
            "columns": [asdict(c) for c in self.columns],
            "keys": [asdict(k) for k in self.keys],
            "indexes": [asdict(i) for i in self.indexes],
            "estimated_rows": self.estimated_rows,
            "size_bytes": self.size_bytes,
            "last_analyzed": self.last_analyzed,
            "approximate": self.approximate,
        }


@dataclass
class CatalogSnapshot:
    environment: str
    connection_id: str
    driver: str
    objects: list[ObjectMeta]
    fetched_at: float
    schemas: list[str]

    def to_public(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "connection_id": self.connection_id,
            "driver": self.driver,
            "fetched_at": self.fetched_at,
            "schemas": self.schemas,
            "object_count": len(self.objects),
            "objects": [o.to_dict() for o in self.objects],
        }


class MetadataCache:
    def __init__(self) -> None:
        self._by_key: dict[str, CatalogSnapshot] = {}

    def get(self, key: str, *, ttl: int) -> CatalogSnapshot | None:
        snap = self._by_key.get(key)
        if not snap:
            return None
        if time.time() - snap.fetched_at > ttl:
            return None
        return snap

    def put(self, key: str, snap: CatalogSnapshot) -> None:
        self._by_key[key] = snap

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._by_key.clear()
        else:
            self._by_key.pop(key, None)


_CACHE = MetadataCache()


def discover_catalog(
    profile: SqlConnectionProfile,
    *,
    environment: str,
    force: bool = False,
    cfg: ExplorerConfig | None = None,
) -> CatalogSnapshot:
    cfg = cfg or get_explorer_config()
    key = f"{environment}:{profile.id}"
    if not force:
        cached = _CACHE.get(key, ttl=cfg.defaults.metadata_cache_ttl_seconds)
        if cached:
            return cached
    if profile.driver == "sqlite":
        snap = _discover_sqlite(profile, environment=environment, cfg=cfg)
    elif profile.driver == "postgresql":
        snap = _discover_postgres(profile, environment=environment, cfg=cfg)
    else:
        raise ExplorerSafetyError(f"Unsupported driver: {profile.driver}")
    _CACHE.put(key, snap)
    return snap


def invalidate_catalog_cache(environment: str | None = None, connection_id: str | None = None) -> None:
    if environment and connection_id:
        _CACHE.invalidate(f"{environment}:{connection_id}")
    else:
        _CACHE.invalidate()


def _discover_sqlite(
    profile: SqlConnectionProfile,
    *,
    environment: str,
    cfg: ExplorerConfig,
) -> CatalogSnapshot:
    path = profile.sqlite_path or ":memory:"
    conn = sqlite3.connect(str(Path(path)) if path != ":memory:" else ":memory:")
    objects: list[ObjectMeta] = []
    try:
        try:
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error:
            pass
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for name, typ in rows:
            obj_type = "view" if typ == "view" else "table"
            cols = _sqlite_columns(conn, name)
            keys = _sqlite_keys(conn, name)
            indexes = _sqlite_indexes(conn, name)
            est = None
            try:
                est = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.Error:
                pass
            objects.append(
                ObjectMeta(
                    schema="main",
                    name=name,
                    object_type=obj_type,
                    columns=cols,
                    keys=keys,
                    indexes=indexes,
                    estimated_rows=est,
                    approximate=False,
                )
            )
    finally:
        conn.close()
    schemas = sorted({o.schema for o in objects})
    return CatalogSnapshot(
        environment=environment,
        connection_id=profile.id,
        driver="sqlite",
        objects=objects,
        fetched_at=time.time(),
        schemas=schemas,
    )


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[ColumnMeta]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    out: list[ColumnMeta] = []
    for r in rows:
        out.append(
            ColumnMeta(
                name=str(r[1]),
                data_type=str(r[2] or "TEXT"),
                nullable=not bool(r[3]),
                default=None if r[4] is None else str(r[4]),
                ordinal=int(r[0]),
            )
        )
    return out


def _sqlite_keys(conn: sqlite3.Connection, table: str) -> list[KeyMeta]:
    keys: list[KeyMeta] = []
    pk_cols = [
        str(r[1])
        for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        if r[5]
    ]
    if pk_cols:
        keys.append(KeyMeta(name="PRIMARY", kind="primary", columns=pk_cols))
    for r in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
        keys.append(
            KeyMeta(
                name=f"fk_{r[0]}",
                kind="foreign",
                columns=[str(r[3])],
                referenced_schema="main",
                referenced_table=str(r[2]),
                referenced_columns=[str(r[4])],
            )
        )
    return keys


def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> list[IndexMeta]:
    out: list[IndexMeta] = []
    for r in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = str(r[1])
        unique = bool(r[2])
        cols = [
            str(c[2])
            for c in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
            if c[2]
        ]
        out.append(IndexMeta(name=name, columns=cols, unique=unique))
    return out


def _discover_postgres(
    profile: SqlConnectionProfile,
    *,
    environment: str,
    cfg: ExplorerConfig,
) -> CatalogSnapshot:
    import psycopg

    conninfo = (
        f"host={profile.host} port={profile.port or 5432} dbname={profile.database} "
        f"user={profile.user} password={profile.password or ''} "
        f"sslmode={profile.sslmode or 'prefer'}"
    )
    conn = psycopg.connect(conninfo, connect_timeout=8, autocommit=False)
    objects: list[ObjectMeta] = []
    try:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        conn.execute("BEGIN READ ONLY")
        # Tables + views
        cur = conn.execute(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_schema, table_name
            """
        )
        base_rows = cur.fetchall()
        for schema, name, ttype in base_rows:
            if is_excluded_schema(str(schema), cfg):
                continue
            obj_type = "view" if ttype == "VIEW" else "table"
            objects.append(ObjectMeta(schema=str(schema), name=str(name), object_type=obj_type))

        # Materialized views
        try:
            mcur = conn.execute(
                """
                SELECT schemaname, matviewname
                FROM pg_matviews
                ORDER BY schemaname, matviewname
                """
            )
            for schema, name in mcur.fetchall():
                if is_excluded_schema(str(schema), cfg):
                    continue
                objects.append(
                    ObjectMeta(
                        schema=str(schema),
                        name=str(name),
                        object_type="materialized_view",
                    )
                )
        except Exception:  # noqa: BLE001
            log.debug("pg_matviews unavailable", exc_info=True)

        # Fetch metadata in bounded catalog-wide queries. Per-object queries made a
        # 390-relation Live catalog require more than 1,500 SSH round trips.
        _enrich_postgres_objects(conn, objects)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        conn.close()

    schemas = sorted({o.schema for o in objects})
    return CatalogSnapshot(
        environment=environment,
        connection_id=profile.id,
        driver="postgresql",
        objects=objects,
        fetched_at=time.time(),
        schemas=schemas,
    )


def _enrich_postgres_objects(conn: Any, objects: list[ObjectMeta]) -> None:
    """Populate a catalog using a bounded number of read-only metadata queries."""
    targets = {(obj.schema, obj.name): obj for obj in objects}
    if not targets:
        return

    rows = conn.execute(
        """
        SELECT table_schema, table_name, column_name, data_type,
               is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        ORDER BY table_schema, table_name, ordinal_position
        """
    ).fetchall()
    for schema, table, name, data_type, nullable, default, ordinal in rows:
        obj = targets.get((str(schema), str(table)))
        if obj is not None:
            obj.columns.append(
                ColumnMeta(
                    name=str(name),
                    data_type=str(data_type or ""),
                    nullable=str(nullable).upper() == "YES",
                    default=None if default is None else str(default)[:200],
                    ordinal=int(ordinal or 0),
                )
            )

    key_lookup: dict[tuple[str, str, str], KeyMeta] = {}
    rows = conn.execute(
        """
        SELECT tc.table_schema, tc.table_name, tc.constraint_name,
               tc.constraint_type, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
        ORDER BY tc.table_schema, tc.table_name, tc.constraint_name, kcu.ordinal_position
        """
    ).fetchall()
    kinds = {"PRIMARY KEY": "primary", "UNIQUE": "unique", "FOREIGN KEY": "foreign"}
    for schema, table, name, constraint_type, column, _ordinal in rows:
        object_key = (str(schema), str(table))
        obj = targets.get(object_key)
        if obj is None:
            continue
        lookup_key = (*object_key, str(name))
        key = key_lookup.get(lookup_key)
        if key is None:
            key = KeyMeta(
                name=str(name),
                kind=kinds.get(str(constraint_type), "unique"),
                columns=[],
            )
            key_lookup[lookup_key] = key
            obj.keys.append(key)
        key.columns.append(str(column))

    rows = conn.execute(
        """
        SELECT tc.table_schema, tc.table_name, tc.constraint_name,
               ccu.table_schema, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
        """
    ).fetchall()
    for schema, table, name, ref_schema, ref_table, ref_column in rows:
        key = key_lookup.get((str(schema), str(table), str(name)))
        if key is not None:
            key.referenced_schema = str(ref_schema)
            key.referenced_table = str(ref_table)
            key.referenced_columns.append(str(ref_column))

    rows = conn.execute(
        """
        SELECT n.nspname, t.relname, i.relname, ix.indisunique,
               array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum))
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_index ix ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE NOT ix.indisprimary
        GROUP BY n.nspname, t.relname, i.relname, ix.indisunique
        ORDER BY n.nspname, t.relname, i.relname
        """
    ).fetchall()
    for schema, table, name, unique, columns in rows:
        obj = targets.get((str(schema), str(table)))
        if obj is not None:
            obj.indexes.append(
                IndexMeta(
                    name=str(name),
                    columns=[str(column) for column in (columns or []) if column],
                    unique=bool(unique),
                )
            )

    rows = conn.execute(
        """
        SELECT n.nspname, c.relname, c.reltuples::bigint,
               pg_total_relation_size(c.oid), s.last_analyze::text
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
        WHERE c.relkind IN ('r', 'p', 'm', 'v')
        """
    ).fetchall()
    for schema, table, estimated, size_bytes, analyzed in rows:
        obj = targets.get((str(schema), str(table)))
        if obj is not None:
            obj.estimated_rows = None if estimated is None else int(estimated)
            obj.size_bytes = None if size_bytes is None else int(size_bytes)
            obj.last_analyzed = analyzed
            obj.approximate = obj.estimated_rows is not None


def _pg_columns(conn: Any, schema: str, table: str) -> list[ColumnMeta]:
    cur = conn.execute(
        """
        SELECT column_name, data_type, is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [
        ColumnMeta(
            name=str(r[0]),
            data_type=str(r[1] or ""),
            nullable=str(r[2]).upper() == "YES",
            default=None if r[3] is None else str(r[3])[:200],
            ordinal=int(r[4] or 0),
        )
        for r in cur.fetchall()
    ]


def _pg_keys(conn: Any, schema: str, table: str) -> list[KeyMeta]:
    keys: list[KeyMeta] = []
    cur = conn.execute(
        """
        SELECT tc.constraint_name, tc.constraint_type, kcu.column_name, kcu.ordinal_position
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
        ORDER BY tc.constraint_name, kcu.ordinal_position
        """,
        (schema, table),
    )
    grouped: dict[str, KeyMeta] = {}
    for name, ctype, col, _ord in cur.fetchall():
        kind = {"PRIMARY KEY": "primary", "UNIQUE": "unique", "FOREIGN KEY": "foreign"}.get(
            str(ctype), "unique"
        )
        if name not in grouped:
            grouped[name] = KeyMeta(name=str(name), kind=kind, columns=[])
        grouped[name].columns.append(str(col))
    # FK references
    fcur = conn.execute(
        """
        SELECT
          tc.constraint_name,
          ccu.table_schema, ccu.table_name, ccu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.constraint_schema = tc.table_schema
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
        """,
        (schema, table),
    )
    for name, rschema, rtable, rcol in fcur.fetchall():
        k = grouped.get(str(name))
        if not k:
            continue
        k.referenced_schema = str(rschema)
        k.referenced_table = str(rtable)
        k.referenced_columns.append(str(rcol))
    return list(grouped.values())


def _pg_indexes(conn: Any, schema: str, table: str) -> list[IndexMeta]:
    cur = conn.execute(
        """
        SELECT i.relname AS index_name,
               ix.indisunique,
               array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS cols
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_index ix ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE n.nspname = %s AND t.relname = %s AND NOT ix.indisprimary
        GROUP BY i.relname, ix.indisunique
        ORDER BY i.relname
        """,
        (schema, table),
    )
    out: list[IndexMeta] = []
    for name, unique, cols in cur.fetchall():
        col_list = [str(c) for c in (cols or []) if c]
        out.append(IndexMeta(name=str(name), columns=col_list, unique=bool(unique)))
    return out


def _pg_stats(
    conn: Any, schema: str, table: str, cfg: ExplorerConfig
) -> tuple[int | None, int | None, str | None, bool]:
    est = None
    size_b = None
    analyzed = None
    approx = True
    try:
        cur = conn.execute(
            """
            SELECT c.reltuples::bigint,
                   pg_total_relation_size(c.oid),
                   s.last_analyze::text
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_stat_all_tables s ON s.relid = c.oid
            WHERE n.nspname = %s AND c.relname = %s
            """,
            (schema, table),
        )
        row = cur.fetchone()
        if row:
            est = int(row[0]) if row[0] is not None else None
            size_b = int(row[1]) if row[1] is not None else None
            analyzed = row[2]
            if est is not None and est < cfg.defaults.approximate_count_threshold:
                # Exact count for modest tables
                q = conn.execute(
                    f'SELECT COUNT(*) FROM "{schema}"."{table}"'
                )
                est = int(q.fetchone()[0])
                approx = False
    except Exception:  # noqa: BLE001
        log.debug("stats failed for %s.%s", schema, table, exc_info=True)
    return est, size_b, analyzed, approx
