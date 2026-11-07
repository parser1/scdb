"""HTTP routes for the control plane and the server-rendered UI."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from api.audit_actions import CLUSTER_DELETE, RESTORE_START
from api.errors import APIError
from api.present import cluster_view, operation_view
from api.schemas import CreateCluster, DeleteCluster, RestoreBody

router = APIRouter()


def _state(request: Request):
    return request.app.state


def _auth(request: Request) -> None:
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    expected = request.app.state.settings.api_key
    if not token or token != expected:
        request.app.state.store.audit(
            "operator",
            "auth.denied",
            None,
            "",
            "failed",
            detail={},
        )
        raise APIError(401, "unauthorized", "Missing or wrong API key")


def _cluster(request: Request, cluster_id: str) -> dict:
    cluster = request.app.state.store.get_cluster(cluster_id)
    if cluster is None or cluster["status"] == "deleted":
        raise APIError(404, "not_found", "Cluster not found")
    return cluster


@router.post("/clusters", status_code=202)
def create_cluster(request: Request, body: CreateCluster):
    _auth(request)
    store = _state(request).store
    if store.list_clusters():
        raise APIError(409, "cluster_exists", "A cluster already exists. Delete it before creating another.")
    cluster, operation = store.create_cluster(body.name, body.location, body.model_dump())
    return {"cluster": cluster_view(store, cluster, include_secret=False), "operation": operation_view(operation)}


@router.get("/clusters")
def list_clusters(request: Request):
    _auth(request)
    store = _state(request).store
    return [cluster_view(store, cluster, include_secret=False) for cluster in store.list_clusters()]


@router.get("/clusters/{cluster_id}")
def get_cluster(request: Request, cluster_id: str):
    _auth(request)
    view = cluster_view(_state(request).store, _cluster(request, cluster_id), include_secret=True)
    cloud = _state(request).cloud
    view["azure_commands"] = cloud.recent_commands() if hasattr(cloud, "recent_commands") else []
    return view


@router.post("/clusters/{cluster_id}/deletion", status_code=202)
def delete_cluster(request: Request, cluster_id: str, body: DeleteCluster):
    _auth(request)
    cluster = _cluster(request, cluster_id)
    if body.confirm != cluster["name"]:
        raise APIError(400, "confirm_mismatch", "confirm must equal the cluster name")
    store = _state(request).store
    if store.has_open_operation(cluster_id):
        raise APIError(409, "operation_in_progress", "Another operation is running")
    operation = store.queue_operation(
        cluster_id,
        "delete_cluster",
        body.model_dump(),
        CLUSTER_DELETE,
    )
    return {"operation": operation_view(operation)}


@router.post("/clusters/{cluster_id}/nodes/{node_id}/restart", status_code=202)
def restart_node(request: Request, cluster_id: str, node_id: str):
    _auth(request)
    _cluster(request, cluster_id)
    store = _state(request).store
    node = store.get_node(node_id)
    if node is None or node["cluster_id"] != cluster_id or node["role"] != "data":
        raise APIError(404, "not_found", "Node not found")
    if store.has_open_operation(cluster_id):
        raise APIError(409, "operation_in_progress", "Another operation is running")
    operation = store.queue_operation(
        cluster_id,
        "restart_node",
        {"node_id": node_id, "mode": "systemctl_restart"},
        "node.restart",
        node_id,
    )
    return {"operation": operation_view(operation)}


@router.post("/clusters/{cluster_id}/nodes/{node_id}/replacement", status_code=202)
def replace_node(request: Request, cluster_id: str, node_id: str):
    _auth(request)
    cluster = _cluster(request, cluster_id)
    store = _state(request).store
    node = store.get_node(node_id)
    if node is None or node["cluster_id"] != cluster_id or node["role"] != "data":
        raise APIError(404, "not_found", "Node not found")
    if store.has_open_operation(cluster_id):
        raise APIError(409, "operation_in_progress", "Another operation is running")
    peers = [
        item
        for item in store.list_nodes(cluster_id)
        if item["role"] == "data" and item["id"] != node_id
    ]
    if any(item["status"] != "up" for item in peers) or cluster["status"] == "degraded":
        raise APIError(409, "quorum_lost", "Two data nodes are down. Replacement is disabled.")
    if not node.get("host_id"):
        raise APIError(409, "missing_host_id", "This node has no stored host id")
    operation = store.queue_operation(
        cluster_id,
        "replace_node",
        {"node_id": node_id},
        "node.replace",
        node_id,
    )
    return {"operation": operation_view(operation)}


_LOG_COMMANDS = {
    "cloud-init": "tail -n 120 /var/log/cloud-init-output.log",
    "scylla": "journalctl -u scylla-server -n 120 --no-pager",
    "agent": "journalctl -u scylla-manager-agent -n 120 --no-pager",
    "docker": "docker compose -f /opt/scylla-ops/compose.yml --project-directory /opt/scylla-ops logs --tail 80",
}
_LOGS_FOR_ROLE = {
    "data": ("cloud-init", "scylla", "agent"),
    "ops": ("cloud-init", "docker"),
}


@router.get("/clusters/{cluster_id}/nodes/{node_id}/logs")
def node_logs(request: Request, cluster_id: str, node_id: str, source: str):
    _auth(request)
    cluster = _cluster(request, cluster_id)
    node = _state(request).store.get_node(node_id)
    if node is None or node["cluster_id"] != cluster_id:
        raise APIError(404, "not_found", "Node not found")
    allowed = _LOGS_FOR_ROLE.get(node["role"], ())
    if source not in allowed:
        raise APIError(400, "unknown_log", "That log is not available on this node")
    script = f"#!/bin/bash\n# dbaas:logs {source}\n{_LOG_COMMANDS[source]}\n"
    try:
        text = _state(request).cloud.run_command(cluster["compute_rg"], node["vm_name"], script)
    except Exception as exc:
        raise APIError(502, "log_unavailable", str(exc)[:500]) from exc
    return {"node_id": node_id, "source": source, "text": text}


@router.get("/clusters/{cluster_id}/health")
def health(request: Request, cluster_id: str):
    _auth(request)
    cluster = _cluster(request, cluster_id)
    nodes = _state(request).store.list_nodes(cluster_id)
    ops = next(node for node in nodes if node["role"] == "ops")
    return {
        "cluster_status": cluster["status"],
        "automation": "frozen" if cluster["status"] == "degraded" else "enabled",
        "ops_status": "down" if ops["status"] == "down" else "up",
        "nodes": [
            {
                "id": node["id"],
                "ordinal": node["ordinal"],
                "status": node["status"],
                "private_ip": node["private_ip"],
            }
            for node in nodes
            if node["role"] == "data"
        ],
    }


@router.post("/clusters/{cluster_id}/backups", status_code=202)
def start_backup(request: Request, cluster_id: str):
    _auth(request)
    _cluster(request, cluster_id)
    store = _state(request).store
    if store.has_open_operation(cluster_id):
        raise APIError(409, "operation_in_progress", "Another operation is running")
    operation = store.queue_operation(cluster_id, "backup", {}, "backup.start")
    return {"operation": operation_view(operation)}


@router.get("/clusters/{cluster_id}/backups")
def list_backups(request: Request, cluster_id: str):
    _auth(request)
    _cluster(request, cluster_id)
    return _state(request).store.list_backups(cluster_id)


@router.post("/clusters/{cluster_id}/restores", status_code=202)
def start_restore(request: Request, cluster_id: str, body: RestoreBody):
    _auth(request)
    cluster = _cluster(request, cluster_id)
    if body.confirm != cluster["name"]:
        raise APIError(400, "confirm_mismatch", "confirm must equal the cluster name")
    if cluster["status"] != "ready":
        raise APIError(409, "not_ready", "Restore requires a ready cluster")
    store = _state(request).store
    backup = store.get_backup(body.backup_id)
    if backup is None or backup["cluster_id"] != cluster_id:
        raise APIError(404, "not_found", "Backup not found")
    if backup["status"] != "succeeded":
        raise APIError(409, "backup_not_ready", "That backup did not succeed")
    if any(node["status"] != "up" for node in store.list_nodes(cluster_id) if node["role"] == "data"):
        raise APIError(409, "node_down", "A data node is down")
    if store.has_open_operation(cluster_id):
        raise APIError(409, "operation_in_progress", "Another operation is running")
    operation = store.queue_operation(
        cluster_id,
        "restore",
        body.model_dump(),
        RESTORE_START,
        body.backup_id,
    )
    return {"operation": operation_view(operation)}


@router.get("/operations/{operation_id}")
def get_operation(request: Request, operation_id: str):
    _auth(request)
    operation = _state(request).store.get_operation(operation_id)
    if operation is None:
        raise APIError(404, "not_found", "Operation not found")
    return operation_view(operation)


@router.get("/audit")
def list_audit(request: Request, cluster_id: str | None = None, limit: int = 100):
    _auth(request)
    return _state(request).store.list_audit(cluster_id, limit)


@router.get("/orphans")
def orphans(request: Request):
    _auth(request)
    state = _state(request)
    known = state.store.known_groups()
    return [name for name in state.cloud.list_tagged_groups() if name not in known]


ui = APIRouter()


@ui.get("/", response_class=HTMLResponse)
def home(request: Request):
    return request.app.state.templates.TemplateResponse(request, "index.html", {"page": "Clusters"})


@ui.get("/clusters/{cluster_id}", response_class=HTMLResponse)
def cluster_page(request: Request, cluster_id: str):
    return request.app.state.templates.TemplateResponse(
        request,
        "cluster.html",
        {"page": "Cluster", "cluster_id": cluster_id},
    )


@ui.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return request.app.state.templates.TemplateResponse(request, "audit.html", {"page": "Audit"})
