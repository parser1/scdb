"""Worker loop, operation dispatch, and the health classifier."""

from __future__ import annotations

import logging
import threading
import time

from api.audit_actions import (
    BACKUP_FINISH,
    CLUSTER_CREATE,
    CLUSTER_DELETE,
    NODE_REPLACE,
    NODE_RESTART,
    OPS_DOWN,
    RECOVERY_FROZEN,
    RESTORE_FINISH,
)
from api.errors import InvalidTransition, Pause, WorkflowError
from worker.flows import Ctx, _nodetool_flag, run_backup, run_create, run_delete, run_replace, run_restart, run_restore

log = logging.getLogger("scylla.worker")

TERMINAL_ACTION = {
    "create_cluster": CLUSTER_CREATE,
    "delete_cluster": CLUSTER_DELETE,
    "restart_node": NODE_RESTART,
    "replace_node": NODE_REPLACE,
    "backup": BACKUP_FINISH,
    "restore": RESTORE_FINISH,
}

RUNNERS = {
    "create_cluster": run_create,
    "delete_cluster": run_delete,
    "restart_node": run_restart,
    "replace_node": run_replace,
    "backup": run_backup,
    "restore": run_restore,
}


def execute(store, cloud, settings, operation_id: str) -> None:
    operation = store.get_operation(operation_id)
    cluster = store.get_cluster(operation["cluster_id"])
    ctx = Ctx(store=store, cloud=cloud, settings=settings, operation=operation, cluster=cluster)
    ctx.refresh()
    try:
        RUNNERS[operation["type"]](ctx)
    except Pause:
        return
    except WorkflowError as exc:
        _fail(store, operation, cluster["id"], str(exc), exc.cluster_status)
        return
    except Exception as exc:
        log.exception("cluster=%s operation=%s failed", cluster["id"], operation_id)
        keep_status = operation["type"] in {"restart_node", "backup"}
        _fail(store, operation, cluster["id"], str(exc), None if keep_status else "error")
        return
    store.finish_operation(operation_id, "succeeded", None)
    fresh = store.get_operation(operation_id)
    store.audit(
        "worker",
        TERMINAL_ACTION[operation["type"]],
        cluster["id"],
        operation["request"].get("node_id", ""),
        "succeeded",
        detail={"operation_id": operation_id, "step": fresh["step"]},
    )


def _fail(store, operation, cluster_id: str, message: str, cluster_status: str | None) -> None:
    store.finish_operation(operation["id"], "failed", message)
    if cluster_status:
        try:
            store.set_cluster_status(cluster_id, cluster_status)
        except InvalidTransition:
            log.warning("cluster=%s could not move to %s", cluster_id, cluster_status)
    store.audit(
        "worker",
        TERMINAL_ACTION[operation["type"]],
        cluster_id,
        operation["request"].get("node_id", ""),
        "failed",
        detail={"operation_id": operation["id"], "error": message},
    )


def _restart_failed(store, cluster_id: str, node_id: str) -> bool:
    return any(
        item["status"] == "failed"
        for item in store.operations_for_target(cluster_id, "restart_node", node_id)
    )


def health_tick(store, cloud, settings) -> None:
    cloud.health_ticks = getattr(cloud, "health_ticks", 0) + 1
    for cluster in store.list_clusters():
        if cluster["status"] not in {"ready", "degraded"}:
            continue
        if store.has_open_operation(cluster["id"]):
            continue
        _classify(store, cloud, cluster)
        _notice_scheduled_backups(store, cloud, cluster)
    _purge_retention(store, cloud)


def _classify(store, cloud, cluster: dict) -> None:
    nodes = store.list_nodes(cluster["id"])
    data = [node for node in nodes if node["role"] == "data"]
    ops = next(node for node in nodes if node["role"] == "ops")
    powers = {
        node["id"]: cloud.power_state(cluster["compute_rg"], node["vm_name"]) for node in data
    }
    deep = getattr(cloud, "force_deep", False) or any(
        state != "running" for state in powers.values()
    ) or cloud.health_ticks % 5 == 0
    nodetool = ""
    if deep and data:
        try:
            nodetool = cloud.run_command(
                cluster["compute_rg"],
                data[0]["vm_name"],
                "#!/bin/bash\n# dbaas:nodetool\nnodetool status\n",
            )
        except Exception:
            nodetool = ""
    unhealthy = []
    plans = []
    for node in data:
        power = powers[node["id"]]
        if power in {"stopped", "deallocated"}:
            unhealthy.append(node)
            plans.append((node, "start_vm"))
        elif power in {"missing", "failed"}:
            unhealthy.append(node)
            plans.append((node, "replace"))
        elif power != "running":
            unhealthy.append(node)
            plans.append((node, "systemctl_restart"))
        elif not deep:
            continue
        else:
            flag = _nodetool_flag(nodetool, node["private_ip"])
            if flag == "UN":
                if node["status"] != "up":
                    store.update_node(node["id"], status="up")
            else:
                unhealthy.append(node)
                mode = "replace" if _restart_failed(store, cluster["id"], node["id"]) else "systemctl_restart"
                plans.append((node, mode))

    ops_power = cloud.power_state(cluster["compute_rg"], ops["vm_name"])
    if ops_power != "running":
        if ops["status"] != "down":
            store.update_node(ops["id"], status="down")
            store.audit("worker", OPS_DOWN, cluster["id"], ops["id"], "succeeded", detail={"power": ops_power})
    elif ops["status"] != "up":
        store.update_node(ops["id"], status="up")

    if len(unhealthy) >= 2:
        for node in unhealthy:
            if node["status"] != "down":
                store.update_node(node["id"], status="down")
        if cluster["status"] != "degraded":
            store.set_cluster_status(cluster["id"], "degraded")
            store.audit(
                "worker",
                RECOVERY_FROZEN,
                cluster["id"],
                "",
                "blocked",
                severity="critical",
                detail={"nodes": [node["id"] for node in unhealthy]},
            )
        return

    if not unhealthy and cluster["status"] == "degraded":
        store.set_cluster_status(cluster["id"], "ready")
        return

    if len(plans) != 1:
        return
    node, mode = plans[0]
    store.update_node(node["id"], status="down")
    if mode == "replace":
        if not node.get("host_id"):
            store.audit(
                "worker",
                NODE_REPLACE,
                cluster["id"],
                node["id"],
                "blocked",
                detail={"reason": "missing host_id"},
            )
            return
        try:
            store.queue_operation(cluster["id"], "replace_node", {"node_id": node["id"]}, "", node["id"])
        except ValueError:
            return
        return
    try:
        store.queue_operation(
            cluster["id"],
            "restart_node",
            {"node_id": node["id"], "mode": mode},
            "",
            node["id"],
        )
    except ValueError:
        return


def _notice_scheduled_backups(store, cloud, cluster: dict) -> None:
    nodes = store.list_nodes(cluster["id"])
    ops = next((node for node in nodes if node["role"] == "ops"), None)
    if ops is None or cloud.power_state(cluster["compute_rg"], ops["vm_name"]) != "running":
        return
    try:
        text = cloud.run_command(
            cluster["compute_rg"],
            ops["vm_name"],
            "#!/bin/bash\n# dbaas:sctool-tasks\n",
        )
    except Exception:
        return
    known = store.known_run_ids(cluster["id"])
    for run_id in text.split():
        if not run_id.startswith("run-") or run_id in known:
            continue
        store.record_backup(
            cluster["id"], None, "scheduled", run_id, "azure:scylla-backup", "succeeded", "scheduled"
        )
        store.audit(
            "worker",
            BACKUP_FINISH,
            cluster["id"],
            "",
            "succeeded",
            detail={"manager_run_id": run_id, "scheduled": True},
        )


def _purge_retention(store, cloud) -> None:
    for cluster in store.clusters_past_retention():
        cloud.delete_group(cluster["backup_rg"])
        store.update_cluster(cluster["id"], backup_retain_until=None)


class Worker:
    def __init__(self, store, cloud, settings):
        self.store = store
        self.cloud = cloud
        self.settings = settings
        self._stop = threading.Event()

    def _run_operation(self) -> bool:
        running = self.store.running_operations()
        if running:
            execute(self.store, self.cloud, self.settings, running[0]["id"])
            return True
        operation = self.store.claim_next_operation()
        if operation:
            execute(self.store, self.cloud, self.settings, operation["id"])
            return True
        return False

    def tick(self) -> None:
        if not self._run_operation():
            health_tick(self.store, self.cloud, self.settings)

    def run_forever(self) -> None:
        next_health = 0.0
        while not self._stop.wait(2):
            try:
                if self._run_operation():
                    continue
                now = time.monotonic()
                if now >= next_health:
                    health_tick(self.store, self.cloud, self.settings)
                    next_health = now + 60
            except Exception:
                log.exception("worker tick failed")

    def stop(self) -> None:
        self._stop.set()
