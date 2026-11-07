"""SQLite catalog for clusters, operations, and the append-only audit log."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from api.errors import InvalidTransition
from api.ids import new_id, redact, utcnow

ALLOWED_STATUS = {
    "pending": {"provisioning", "error"},
    "provisioning": {"ready", "error"},
    "ready": {"degraded", "restoring", "deleting", "error"},
    "degraded": {"ready", "deleting", "error", "restoring"},
    "restoring": {"ready", "error"},
    "deleting": {"deleted", "error"},
    "error": {"deleting"},
    "deleted": set(),
}

CLUSTER_FIELDS = {
    "compute_rg",
    "backup_rg",
    "storage_account",
    "manager_install",
    "io_profile",
    "cql_user",
    "cql_secret",
    "manager_secret",
    "manager_agent_token",
    "grafana_url",
    "grafana_password",
    "repair_task_id",
    "backup_schedule_id",
    "backup_retain_until",
    "layout_json",
}

NODE_FIELDS = {
    "vm_name",
    "nic_name",
    "data_disk_name",
    "private_ip",
    "public_ip",
    "host_id",
    "status",
    "nic_id",
    "vm_id",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS clusters (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL,
  location TEXT NOT NULL,
  compute_rg TEXT,
  backup_rg TEXT,
  storage_account TEXT,
  manager_install TEXT,
  io_profile TEXT,
  cql_user TEXT,
  cql_secret TEXT,
  manager_secret TEXT,
  manager_agent_token TEXT,
  grafana_url TEXT,
  grafana_password TEXT,
  repair_task_id TEXT,
  backup_schedule_id TEXT,
  backup_retain_until TEXT,
  layout_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS clusters_one_live_name
  ON clusters(name) WHERE status != 'deleted';
CREATE TABLE IF NOT EXISTS nodes (
  id TEXT PRIMARY KEY,
  cluster_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  role TEXT NOT NULL,
  vm_name TEXT,
  nic_name TEXT,
  data_disk_name TEXT,
  private_ip TEXT,
  public_ip TEXT,
  host_id TEXT,
  status TEXT NOT NULL,
  nic_id TEXT,
  vm_id TEXT
);
CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  cluster_id TEXT,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  step TEXT,
  error_message TEXT,
  request_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
  id TEXT PRIMARY KEY,
  cluster_id TEXT NOT NULL,
  operation_id TEXT,
  manager_task_id TEXT,
  manager_run_id TEXT,
  location TEXT,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  cluster_id TEXT,
  target TEXT,
  outcome TEXT NOT NULL,
  severity TEXT NOT NULL,
  detail_json TEXT
);
"""

NODE_PLAN = (
    ("ops", -1, "10.0.1.4"),
    ("data", 0, "10.0.1.10"),
    ("data", 1, "10.0.1.11"),
    ("data", 2, "10.0.1.12"),
)


def _short(cluster_id: str) -> str:
    return "".join(ch for ch in cluster_id.lower() if ch.isalnum())[-8:]


class Store:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[None]:
        with self.lock:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def create_cluster(self, name: str, location: str, request: dict) -> tuple[dict, dict]:
        with self.tx():
            live = self.conn.execute(
                "SELECT id FROM clusters WHERE status != 'deleted' LIMIT 1"
            ).fetchone()
            if live:
                raise ValueError("cluster_exists")
            now = utcnow()
            cluster_id = new_id("c_")
            short = _short(cluster_id)
            cluster = {
                "id": cluster_id,
                "name": name,
                "status": "pending",
                "location": location,
                "compute_rg": f"rg-scylla-{short}",
                "backup_rg": f"rg-scylla-{short}-bak",
                "storage_account": f"st{short}",
                "manager_install": None,
                "io_profile": None,
                "cql_user": None,
                "cql_secret": None,
                "manager_secret": None,
                "manager_agent_token": None,
                "grafana_url": None,
                "grafana_password": None,
                "repair_task_id": None,
                "backup_schedule_id": None,
                "backup_retain_until": None,
                "layout_json": None,
                "created_at": now,
                "updated_at": now,
            }
            columns = ", ".join(cluster)
            placeholders = ", ".join("?" for _ in cluster)
            self.conn.execute(
                f"INSERT INTO clusters ({columns}) VALUES ({placeholders})",
                tuple(cluster.values()),
            )
            for role, ordinal, ip in NODE_PLAN:
                suffix = "ops" if role == "ops" else f"n{ordinal}"
                vm_name = f"scylla-{short}-{suffix}"
                self.conn.execute(
                    """INSERT INTO nodes
                    (id, cluster_id, ordinal, role, vm_name, nic_name, data_disk_name,
                     private_ip, public_ip, host_id, status, nic_id, vm_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'provisioning', NULL, NULL)""",
                    (
                        new_id("n_"),
                        cluster_id,
                        ordinal,
                        role,
                        vm_name,
                        f"{vm_name}-nic",
                        f"{vm_name}-data" if role == "data" else None,
                        ip,
                    ),
                )
            operation = self._insert_operation(cluster_id, "create_cluster", request)
            self._audit(
                actor="operator",
                action="cluster.create",
                cluster_id=cluster_id,
                target="",
                outcome="accepted",
                severity="info",
                detail={"operation_id": operation["id"]},
            )
            self._set_cluster_status(cluster_id, "provisioning")
            cluster["status"] = "provisioning"
            return cluster, operation

    def _insert_operation(self, cluster_id: str | None, op_type: str, request: dict) -> dict:
        open_row = None
        if cluster_id:
            open_row = self.conn.execute(
                """SELECT id FROM operations
                   WHERE cluster_id = ? AND status IN ('queued', 'running') LIMIT 1""",
                (cluster_id,),
            ).fetchone()
        if open_row:
            raise ValueError("operation_in_progress")
        now = utcnow()
        operation = {
            "id": new_id("op_"),
            "cluster_id": cluster_id,
            "type": op_type,
            "status": "queued",
            "step": None,
            "error_message": None,
            "request_json": json.dumps(redact(request)),
            "created_at": now,
            "updated_at": now,
        }
        columns = ", ".join(operation)
        placeholders = ", ".join("?" for _ in operation)
        self.conn.execute(
            f"INSERT INTO operations ({columns}) VALUES ({placeholders})",
            tuple(operation.values()),
        )
        operation["request"] = json.loads(operation["request_json"])
        return operation

    def queue_operation(self, cluster_id: str, op_type: str, request: dict, action: str, target: str = "") -> dict:
        with self.tx():
            operation = self._insert_operation(cluster_id, op_type, request)
            if action:
                self._audit(
                    actor="operator",
                    action=action,
                    cluster_id=cluster_id,
                    target=target,
                    outcome="accepted",
                    severity="info",
                    detail={"operation_id": operation["id"]},
                )
            return operation

    def get_cluster(self, cluster_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        return dict(row) if row else None

    def list_clusters(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM clusters WHERE status != 'deleted' ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]

    def update_cluster(self, cluster_id: str, **fields) -> None:
        unknown = set(fields) - CLUSTER_FIELDS
        if unknown:
            raise ValueError(f"unknown cluster fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [utcnow(), cluster_id]
        with self.tx():
            self.conn.execute(
                f"UPDATE clusters SET {assignments}, updated_at = ? WHERE id = ?",
                values,
            )

    def set_cluster_status(self, cluster_id: str, status: str) -> None:
        with self.tx():
            self._set_cluster_status(cluster_id, status)

    def _set_cluster_status(self, cluster_id: str, status: str) -> None:
        row = self.conn.execute("SELECT status FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
        if row is None:
            raise KeyError(cluster_id)
        current = row["status"]
        if current == status:
            return
        if status not in ALLOWED_STATUS[current]:
            raise InvalidTransition(f"{current} -> {status}")
        self.conn.execute(
            "UPDATE clusters SET status = ?, updated_at = ? WHERE id = ?",
            (status, utcnow(), cluster_id),
        )

    def list_nodes(self, cluster_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE cluster_id = ? ORDER BY ordinal",
            (cluster_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_node(self, node_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return dict(row) if row else None

    def update_node(self, node_id: str, **fields) -> None:
        unknown = set(fields) - NODE_FIELDS
        if unknown:
            raise ValueError(f"unknown node fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [node_id]
        with self.tx():
            self.conn.execute(
                f"UPDATE nodes SET {assignments} WHERE id = ?",
                values,
            )

    def get_operation(self, operation_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        return self._operation(row) if row else None

    def latest_operation(self, cluster_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM operations WHERE cluster_id = ? ORDER BY created_at DESC LIMIT 1",
            (cluster_id,),
        ).fetchone()
        return self._operation(row) if row else None

    def claim_next_operation(self) -> dict | None:
        with self.tx():
            row = self.conn.execute(
                "SELECT * FROM operations WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = utcnow()
            self.conn.execute(
                "UPDATE operations SET status = 'running', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            updated = self.conn.execute("SELECT * FROM operations WHERE id = ?", (row["id"],)).fetchone()
            return self._operation(updated)

    def running_operations(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE status = 'running' ORDER BY created_at"
        ).fetchall()
        return [self._operation(row) for row in rows]

    def set_operation_step(self, operation_id: str, step: str) -> None:
        with self.tx():
            self.conn.execute(
                "UPDATE operations SET step = ?, updated_at = ? WHERE id = ?",
                (step, utcnow(), operation_id),
            )

    def finish_operation(self, operation_id: str, status: str, error_message: str | None) -> None:
        with self.tx():
            self.conn.execute(
                """UPDATE operations
                   SET status = ?, error_message = ?, updated_at = ?
                   WHERE id = ?""",
                (status, error_message, utcnow(), operation_id),
            )

    def has_open_operation(self, cluster_id: str) -> bool:
        row = self.conn.execute(
            """SELECT 1 FROM operations
               WHERE cluster_id = ? AND status IN ('queued', 'running') LIMIT 1""",
            (cluster_id,),
        ).fetchone()
        return row is not None

    def operations_for_target(self, cluster_id: str, op_type: str, node_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM operations
               WHERE cluster_id = ? AND type = ? ORDER BY created_at""",
            (cluster_id, op_type),
        ).fetchall()
        matched = []
        for row in rows:
            operation = self._operation(row)
            if operation["request"].get("node_id") == node_id:
                matched.append(operation)
        return matched

    def record_backup(
        self,
        cluster_id: str,
        operation_id: str | None,
        manager_task_id: str | None,
        manager_run_id: str | None,
        location: str,
        status: str,
        note: str = "",
    ) -> dict:
        now = utcnow()
        backup = {
            "id": new_id("b_"),
            "cluster_id": cluster_id,
            "operation_id": operation_id,
            "manager_task_id": manager_task_id,
            "manager_run_id": manager_run_id,
            "location": location,
            "status": status,
            "started_at": now,
            "finished_at": now if status != "running" else None,
            "note": note,
        }
        with self.tx():
            columns = ", ".join(backup)
            placeholders = ", ".join("?" for _ in backup)
            self.conn.execute(
                f"INSERT INTO backups ({columns}) VALUES ({placeholders})",
                tuple(backup.values()),
            )
        return backup

    def list_backups(self, cluster_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM backups WHERE cluster_id = ? ORDER BY started_at DESC",
            (cluster_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_backup(self, backup_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
        return dict(row) if row else None

    def known_run_ids(self, cluster_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT manager_run_id FROM backups WHERE cluster_id = ? AND manager_run_id IS NOT NULL",
            (cluster_id,),
        ).fetchall()
        return {row["manager_run_id"] for row in rows}

    def known_groups(self) -> set[str]:
        rows = self.conn.execute(
            "SELECT status, compute_rg, backup_rg, backup_retain_until FROM clusters"
        ).fetchall()
        known = set()
        for row in rows:
            if row["status"] != "deleted":
                known.add(row["compute_rg"])
                known.add(row["backup_rg"])
            elif row["backup_retain_until"]:
                known.add(row["backup_rg"])
        return {name for name in known if name}

    def clusters_past_retention(self, now: str | None = None) -> list[dict]:
        moment = now or utcnow()
        rows = self.conn.execute(
            """SELECT * FROM clusters
               WHERE status = 'deleted'
                 AND backup_retain_until IS NOT NULL
                 AND backup_retain_until <= ?""",
            (moment,),
        ).fetchall()
        return [dict(row) for row in rows]

    def audit(
        self,
        actor: str,
        action: str,
        cluster_id: str | None,
        target: str,
        outcome: str,
        severity: str = "info",
        detail: dict | None = None,
    ) -> None:
        with self.tx():
            self._audit(actor, action, cluster_id, target, outcome, severity, detail)

    def _audit(self, actor, action, cluster_id, target, outcome, severity, detail) -> None:
        self.conn.execute(
            """INSERT INTO audit_events
               (at, actor, action, cluster_id, target, outcome, severity, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                utcnow(),
                actor,
                action,
                cluster_id,
                target,
                outcome,
                severity,
                json.dumps(redact(detail or {})),
            ),
        )

    def list_audit(self, cluster_id: str | None, limit: int) -> list[dict]:
        limit = max(1, min(limit, 500))
        if cluster_id:
            rows = self.conn.execute(
                """SELECT * FROM audit_events WHERE cluster_id = ?
                   ORDER BY id DESC LIMIT ?""",
                (cluster_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
            events.append(item)
        return events

    def _operation(self, row: sqlite3.Row) -> dict:
        operation = dict(row)
        operation["request"] = json.loads(operation["request_json"] or "{}")
        return operation


def retention_deadline(days: int = 7) -> str:
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
