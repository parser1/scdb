"""Catalog invariants, workflows, API, and health."""

from api.audit_actions import AUDIT_ACTIONS
from api.errors import InvalidTransition
from api.ids import redact


def test_illegal_transition(env):
    response = env.client.post(
        "/api/v1/clusters",
        json={"name": "demo", "location": "eastus"},
        headers=env.auth,
    )
    cluster_id = response.json()["cluster"]["id"]
    try:
        env.store.set_cluster_status(cluster_id, "deleted")
    except InvalidTransition:
        return
    raise AssertionError("provisioning -> deleted should be rejected")


def test_second_cluster_rejected_by_store(env):
    env.client.post("/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth)
    try:
        env.store.create_cluster("other", "eastus", {"name": "other"})
    except ValueError as exc:
        assert str(exc) == "cluster_exists"
        return
    raise AssertionError("expected cluster_exists")


def test_create_reaches_ready_and_delete_keeps_backups(env):
    created = env.client.post(
        "/api/v1/clusters",
        json={"name": "demo", "location": "eastus"},
        headers=env.auth,
    )
    assert created.status_code == 202
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    cluster = env.store.get_cluster(cluster_id)
    assert cluster["status"] == "ready"
    assert cluster["io_profile"] == "standard"
    assert cluster["backup_schedule_id"] == "daily"
    assert cluster["repair_task_id"] == "weekly"
    hosts = [node["host_id"] for node in env.store.list_nodes(cluster_id) if node["role"] == "data"]
    assert len(hosts) == 3 and all(hosts)
    detail = env.client.get(f"/api/v1/clusters/{cluster_id}", headers=env.auth)
    assert detail.json()["cql"]["password"] == cluster["cql_secret"]
    blob = "\n".join(str(event["detail"]) for event in env.store.list_audit(cluster_id, 100))
    assert cluster["cql_secret"] not in blob
    operation = env.store.get_operation(created.json()["operation"]["id"])
    assert "cql_secret" not in operation["request_json"]
    auth = next(cmd for cmd in env.cloud.commands if "dbaas:configure-auth" in cmd["script"])
    assert "/var/lib/scylla/cql.m" in auth["script"]
    assert "cassandra -p cassandra" not in auth["script"]
    assert cluster["cql_secret"] in auth["script"]

    backup_rg = cluster["backup_rg"]
    compute_rg = cluster["compute_rg"]
    assert compute_rg in env.cloud.groups
    deleted = env.client.post(
        f"/api/v1/clusters/{cluster_id}/deletion",
        json={"confirm": "demo", "delete_backups": False},
        headers=env.auth,
    )
    assert deleted.status_code == 202
    env.worker.tick()
    finished = env.store.get_cluster(cluster_id)
    assert finished["status"] == "deleted"
    assert finished["backup_retain_until"]
    assert compute_rg not in env.cloud.groups
    assert backup_rg in env.cloud.groups


def test_worker_resumes_a_paused_create(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    env.cloud.stop_after = "validate"
    env.worker.tick()
    operation = env.store.get_operation(created.json()["operation"]["id"])
    assert operation["status"] == "running"
    assert operation["step"] == "validate"
    env.worker.tick()
    assert env.store.get_cluster(created.json()["cluster"]["id"])["status"] == "ready"


def test_delete_failure_audited(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    env.cloud.fail_steps.add("delete_compute_group")
    queued = env.client.post(
        f"/api/v1/clusters/{cluster_id}/deletion",
        json={"confirm": "demo"},
        headers=env.auth,
    )
    env.worker.tick()
    operation = env.store.get_operation(queued.json()["operation"]["id"])
    assert operation["status"] == "failed"
    assert env.store.get_cluster(cluster_id)["status"] == "error"
    actions = [event["action"] for event in env.store.list_audit(cluster_id, 50)]
    assert "cluster.delete" in actions


def test_api_auth_confirm_and_conflict(env):
    missing = env.client.post("/api/v1/clusters", json={"name": "demo", "location": "eastus"})
    assert missing.status_code == 401
    bad = env.client.post("/api/v1/clusters", json={"name": "NOPE"}, headers=env.auth)
    assert bad.status_code == 400
    first = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    assert first.status_code == 202
    second = env.client.post(
        "/api/v1/clusters", json={"name": "other", "location": "eastus"}, headers=env.auth
    )
    assert second.status_code == 409
    cluster_id = first.json()["cluster"]["id"]
    wrong = env.client.post(
        f"/api/v1/clusters/{cluster_id}/deletion",
        json={"confirm": "nope"},
        headers=env.auth,
    )
    assert wrong.status_code == 400
    env.worker.tick()
    right = env.client.post(
        f"/api/v1/clusters/{cluster_id}/deletion",
        json={"confirm": "demo"},
        headers=env.auth,
    )
    assert right.status_code == 202


def test_redact_helper():
    cleaned = redact({"password": "secret", "name": "demo", "nested": {"api_key": "k"}})
    assert cleaned["password"] == "<redacted>"
    assert cleaned["nested"]["api_key"] == "<redacted>"
    assert cleaned["name"] == "demo"
    assert "secret" not in str(cleaned)


def test_pages_render(env):
    for path in ("/", "/audit", "/clusters/c_preview"):
        page = env.client.get(path)
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
    home = env.client.get("/")
    assert "Create cluster" in home.text
    assert "Orphan resource groups" in home.text
    detail = env.client.get("/clusters/c_preview")
    assert "overwrites keyspace demo" in detail.text
    audit = env.client.get("/audit")
    assert "Audit" in audit.text


def test_orphans(env):
    env.cloud.extra_groups.append("rg-forgotten")
    response = env.client.get("/api/v1/orphans", headers=env.auth)
    assert response.status_code == 200
    assert response.json() == ["rg-forgotten"]


def test_health_freeze_restart_and_replace(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    nodes = [node for node in env.store.list_nodes(cluster_id) if node["role"] == "data"]
    env.cloud.power[nodes[0]["vm_name"]] = "stopped"
    env.cloud.power[nodes[1]["vm_name"]] = "stopped"
    env.worker.tick()
    assert env.store.get_cluster(cluster_id)["status"] == "degraded"
    frozen = [event for event in env.store.list_audit(cluster_id, 20) if event["action"] == "recovery.frozen"]
    assert len(frozen) == 1 and frozen[0]["severity"] == "critical"
    assert not env.store.has_open_operation(cluster_id)
    env.worker.tick()
    frozen_again = [event for event in env.store.list_audit(cluster_id, 20) if event["action"] == "recovery.frozen"]
    assert len(frozen_again) == 1

    env.cloud.power[nodes[0]["vm_name"]] = "running"
    env.cloud.power[nodes[1]["vm_name"]] = "running"
    env.store.update_node(nodes[0]["id"], status="up")
    env.store.update_node(nodes[1]["id"], status="up")
    env.store.update_node(nodes[2]["id"], status="up")
    env.store.set_cluster_status(cluster_id, "ready")
    env.cloud.node_states[nodes[2]["vm_name"]] = "DN"
    env.cloud.restart_fails = True
    env.worker.tick()
    restart = env.store.claim_next_operation()
    assert restart is None or restart["type"] == "restart_node"
    # health queued the restart; this tick already ran health because no op was
    # running at the start. The queued restart is claimed on the next tick.
    env.worker.tick()
    failed = [
        op for op in env.store.operations_for_target(cluster_id, "restart_node", nodes[2]["id"])
    ]
    assert failed and failed[-1]["status"] == "failed"
    assert env.store.get_cluster(cluster_id)["status"] == "ready"
    env.worker.tick()
    env.worker.tick()
    replaced = env.store.operations_for_target(cluster_id, "replace_node", nodes[2]["id"])
    assert replaced and replaced[-1]["status"] == "succeeded"
    node = env.store.get_node(nodes[2]["id"])
    assert node["private_ip"] == "10.0.1.12"
    assert node["nic_id"]
    assert nodes[2]["vm_name"] in env.cloud.deleted_vms
    custom = [
        item["params"]["customData"]
        for item in env.cloud.deployments
        if item["module"] == "data-vm" and "replace_node_first_boot" in item["params"]["customData"]
    ]
    assert custom and nodes[2]["host_id"] in custom[-1]


def test_replace_refuses_without_quorum(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    nodes = [node for node in env.store.list_nodes(cluster_id) if node["role"] == "data"]
    env.store.update_node(nodes[1]["id"], status="down")
    response = env.client.post(
        f"/api/v1/clusters/{cluster_id}/nodes/{nodes[0]['id']}/replacement",
        headers=env.auth,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "quorum_lost"


def test_backup_partial_and_restore(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    nodes = env.store.list_nodes(cluster_id)
    data = [node for node in nodes if node["role"] == "data"]
    env.store.update_node(data[2]["id"], status="down")
    queued = env.client.post(f"/api/v1/clusters/{cluster_id}/backups", headers=env.auth)
    assert queued.status_code == 202
    env.worker.tick()
    backups = env.store.list_backups(cluster_id)
    assert backups[0]["status"] == "succeeded"
    assert "partial" in backups[0]["note"]
    env.store.update_node(data[2]["id"], status="up")
    bad = env.client.post(
        f"/api/v1/clusters/{cluster_id}/restores",
        json={"backup_id": backups[0]["id"], "confirm": "nope"},
        headers=env.auth,
    )
    assert bad.status_code == 400
    env.cloud.count_error = True
    smoke = env.client.post(
        f"/api/v1/clusters/{cluster_id}/restores",
        json={"backup_id": backups[0]["id"], "confirm": "demo"},
        headers=env.auth,
    )
    env.worker.tick()
    operation = env.store.get_operation(smoke.json()["operation"]["id"])
    assert operation["status"] == "failed"
    assert "check did not" in operation["error_message"]
    assert env.store.get_cluster(cluster_id)["status"] == "ready"
    env.cloud.count_error = False
    again = env.client.post(
        f"/api/v1/clusters/{cluster_id}/restores",
        json={"backup_id": backups[0]["id"], "confirm": "demo"},
        headers=env.auth,
    )
    env.worker.tick()
    assert env.store.get_operation(again.json()["operation"]["id"])["status"] == "succeeded"


def test_scheduled_backup_and_retention(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    env.cloud.manager_runs.append("run-scheduled")
    env.worker.tick()
    backups = env.store.list_backups(cluster_id)
    assert any(item["manager_run_id"] == "run-scheduled" for item in backups)
    cluster = env.store.get_cluster(cluster_id)
    env.store.set_cluster_status(cluster_id, "deleting")
    env.store.set_cluster_status(cluster_id, "deleted")
    env.store.update_cluster(cluster_id, backup_retain_until="2000-01-01T00:00:00Z")
    env.cloud.groups.add(cluster["backup_rg"])
    env.worker.tick()
    assert cluster["backup_rg"] not in env.cloud.groups


def test_node_logs_fetch_one_source(env):
    created = env.client.post(
        "/api/v1/clusters", json={"name": "demo", "location": "eastus"}, headers=env.auth
    )
    cluster_id = created.json()["cluster"]["id"]
    env.worker.tick()
    nodes = env.store.list_nodes(cluster_id)
    data = next(node for node in nodes if node["role"] == "data" and node["ordinal"] == 0)
    ops = next(node for node in nodes if node["role"] == "ops")
    logs = env.client.get(
        f"/api/v1/clusters/{cluster_id}/nodes/{data['id']}/logs",
        params={"source": "scylla"},
        headers=env.auth,
    )
    assert logs.status_code == 200
    assert logs.json()["source"] == "scylla"
    assert "journalctl -u scylla-server" in logs.json()["text"]
    refused = env.client.get(
        f"/api/v1/clusters/{cluster_id}/nodes/{ops['id']}/logs",
        params={"source": "scylla"},
        headers=env.auth,
    )
    assert refused.status_code == 400


def test_audit_names_match_the_doc():
    import re
    from pathlib import Path

    text = Path("docs/workflow/observability-and-audit.md").read_text()
    found = set(re.findall(r"`([a-z]+\.[a-z_]+)`", text))
    assert found == set(AUDIT_ACTIONS)
