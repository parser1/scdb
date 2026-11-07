"""JSON views of catalog rows."""

from __future__ import annotations


def operation_view(operation: dict | None) -> dict | None:
    if not operation:
        return None
    return {
        "id": operation["id"],
        "type": operation["type"],
        "status": operation["status"],
        "step": operation.get("step"),
        "error_message": operation.get("error_message"),
        "updated_at": operation.get("updated_at"),
    }


def node_view(node: dict) -> dict:
    return {
        "id": node["id"],
        "ordinal": node["ordinal"],
        "role": node["role"],
        "status": node["status"],
        "private_ip": node.get("private_ip"),
        "public_ip": node.get("public_ip"),
        "vm_name": node.get("vm_name"),
    }


def cluster_view(store, cluster: dict, include_secret: bool) -> dict:
    nodes = store.list_nodes(cluster["id"])
    data0 = next((node for node in nodes if node["ordinal"] == 0), None)
    contact = None
    if data0 and cluster.get("cql_user") and cluster["status"] == "ready":
        contact = {
            "host": data0.get("public_ip") or data0.get("private_ip"),
            "port": 9042,
            "username": cluster["cql_user"],
            "password": cluster.get("cql_secret") if include_secret else None,
            "keyspace": "demo",
        }
    backups = store.list_backups(cluster["id"])
    return {
        "id": cluster["id"],
        "name": cluster["name"],
        "status": cluster["status"],
        "location": cluster["location"],
        "grafana_url": cluster.get("grafana_url"),
        "io_profile": cluster.get("io_profile"),
        "manager_install": cluster.get("manager_install"),
        "nodes": [node_view(node) for node in nodes],
        "cql": contact,
        "latest_backup": backups[0] if backups else None,
        "operation": operation_view(store.latest_operation(cluster["id"])),
    }
