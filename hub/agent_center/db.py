"""SQLite persistence for Agent Center runs and saved prompts."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_agent_center_initial",
        """
        CREATE TABLE IF NOT EXISTS agent_prompts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Untitled prompt',
            body TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'ask',
            tags_json TEXT NOT NULL DEFAULT '[]',
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_prompts_updated ON agent_prompts(updated_at DESC);

        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            agent_label TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            repository_ids_json TEXT NOT NULL DEFAULT '[]',
            prompt TEXT NOT NULL DEFAULT '',
            packed_prompt TEXT NOT NULL DEFAULT '',
            context_json TEXT NOT NULL DEFAULT '{}',
            answer TEXT NOT NULL DEFAULT '',
            logs TEXT NOT NULL DEFAULT '',
            referenced_files_json TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            pid INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
        """,
    ),
    (
        "002_agent_center_openai_fields",
        """
        ALTER TABLE agent_runs ADD COLUMN tool_activity_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE agent_runs ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    (
        "003_assistant_profiles",
        """
        ALTER TABLE agent_prompts ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'okarun';
        ALTER TABLE agent_runs ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'okarun';
        ALTER TABLE agent_runs ADD COLUMN conversation_id TEXT NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_agent_runs_profile_created
            ON agent_runs(profile_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS agent_conversations (
            id TEXT PRIMARY KEY,
            profile_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'New conversation',
            summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_conversations_profile_updated
            ON agent_conversations(profile_id, updated_at DESC);
        """,
    ),
    (
        "004_ai_connections",
        """
        CREATE TABLE IF NOT EXISTS agent_connections (
            agent_id TEXT PRIMARY KEY,
            disconnected INTEGER NOT NULL DEFAULT 0,
            last_check TEXT NOT NULL DEFAULT '',
            last_successful_check TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        "005_perf_indexes",
        """
        CREATE INDEX IF NOT EXISTS idx_agent_prompts_profile_updated
            ON agent_prompts(profile_id, updated_at DESC);
        """,
    ),
    (
        "006_airix_routing_history",
        """
        CREATE TABLE IF NOT EXISTS airix_routing_events (
            id TEXT PRIMARY KEY,
            workspace TEXT NOT NULL DEFAULT 'work',
            created_at TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            adapter_id TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL DEFAULT 'general',
            status TEXT NOT NULL,
            outcome TEXT NOT NULL,
            retries INTEGER NOT NULL DEFAULT 0,
            runtime_ms INTEGER NOT NULL DEFAULT 0,
            estimated_usage TEXT NOT NULL DEFAULT '',
            actual_tokens INTEGER,
            usage_source TEXT NOT NULL DEFAULT 'estimate',
            t0_llm_avoided INTEGER NOT NULL DEFAULT 0,
            fallback_from TEXT NOT NULL DEFAULT '',
            escalated_to TEXT NOT NULL DEFAULT '',
            prompt_fingerprint TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            partial_summary TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_airix_events_ws_created
            ON airix_routing_events(workspace, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_airix_events_provider_task
            ON airix_routing_events(workspace, provider_id, task_type);
        CREATE INDEX IF NOT EXISTS idx_airix_events_fingerprint
            ON airix_routing_events(workspace, prompt_fingerprint, created_at DESC);

        CREATE TABLE IF NOT EXISTS airix_routing_findings (
            id TEXT PRIMARY KEY,
            workspace TEXT NOT NULL DEFAULT 'work',
            created_at TEXT NOT NULL,
            task_type TEXT NOT NULL,
            keywords_json TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL,
            provider_id TEXT NOT NULL DEFAULT '',
            source_event_id TEXT NOT NULL DEFAULT '',
            hit_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_airix_findings_ws_task
            ON airix_routing_findings(workspace, task_type, created_at DESC);

        CREATE TABLE IF NOT EXISTS airix_routing_provider_stats (
            workspace TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            task_type TEXT NOT NULL,
            successes INTEGER NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0,
            cancels INTEGER NOT NULL DEFAULT 0,
            retries_total INTEGER NOT NULL DEFAULT 0,
            runtime_ms_total INTEGER NOT NULL DEFAULT 0,
            tokens_total INTEGER NOT NULL DEFAULT 0,
            t0_avoided INTEGER NOT NULL DEFAULT 0,
            fallbacks INTEGER NOT NULL DEFAULT 0,
            escalations INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace, provider_id, task_type)
        );
        """,
    ),
    (
        "007_airix_routing_phase4",
        """
        ALTER TABLE airix_routing_events ADD COLUMN actor TEXT NOT NULL DEFAULT 'owner';
        ALTER TABLE airix_routing_findings ADD COLUMN actor TEXT NOT NULL DEFAULT 'owner';

        CREATE TABLE IF NOT EXISTS airix_routing_sessions (
            id TEXT PRIMARY KEY,
            workspace TEXT NOT NULL DEFAULT 'work',
            actor TEXT NOT NULL DEFAULT 'owner',
            prompt_fingerprint TEXT NOT NULL DEFAULT '',
            prompt_preview TEXT NOT NULL DEFAULT '',
            role_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            plan_json TEXT NOT NULL DEFAULT '[]',
            completed_steps_json TEXT NOT NULL DEFAULT '[]',
            findings_json TEXT NOT NULL DEFAULT '[]',
            partial_summary TEXT NOT NULL DEFAULT '',
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            actual_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_airix_sessions_ws_actor
            ON airix_routing_sessions(workspace, actor, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_airix_sessions_fp
            ON airix_routing_sessions(workspace, actor, prompt_fingerprint);
        CREATE INDEX IF NOT EXISTS idx_airix_events_ws_actor_created
            ON airix_routing_events(workspace, actor, created_at DESC);
        """,
    ),
    (
        "008_airix_routing_phase5",
        """
        ALTER TABLE airix_routing_events ADD COLUMN input_tokens INTEGER;
        ALTER TABLE airix_routing_events ADD COLUMN output_tokens INTEGER;
        ALTER TABLE airix_routing_events ADD COLUMN estimated_tokens INTEGER;
        ALTER TABLE airix_routing_events ADD COLUMN estimated_cost_usd REAL;
        ALTER TABLE airix_routing_events ADD COLUMN actual_cost_usd REAL;
        ALTER TABLE airix_routing_events ADD COLUMN findings_reused_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE airix_routing_events ADD COLUMN rbac_role TEXT NOT NULL DEFAULT '';
        ALTER TABLE airix_routing_events ADD COLUMN permission_denied INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS airix_routing_acl (
            workspace TEXT NOT NULL,
            actor TEXT NOT NULL,
            role_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (workspace, actor)
        );
        CREATE INDEX IF NOT EXISTS idx_airix_acl_ws_role
            ON airix_routing_acl(workspace, role_id);
        """,
    ),
    (
        "009_airix_usage_telemetry",
        """
        ALTER TABLE airix_routing_events ADD COLUMN cached_tokens INTEGER;
        ALTER TABLE airix_routing_events ADD COLUMN execution_type TEXT NOT NULL DEFAULT '';
        ALTER TABLE airix_routing_events ADD COLUMN llm_invoked INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE airix_routing_events ADD COLUMN model TEXT NOT NULL DEFAULT '';
        ALTER TABLE airix_routing_events ADD COLUMN child_ai_run_id TEXT NOT NULL DEFAULT '';
        ALTER TABLE airix_routing_events ADD COLUMN tools_used_json TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE airix_routing_events ADD COLUMN telemetry_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
]


def default_agent_db_path() -> Path:
    return ROOT_DIR / "data" / "agent_center.db"


class AgentCenterDb:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_agent_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r[0] for r in conn.execute("SELECT id FROM schema_migrations").fetchall()}
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            for mid, sql in _MIGRATIONS:
                if mid in applied:
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(id, applied_at) VALUES (?, ?)", (mid, now))
