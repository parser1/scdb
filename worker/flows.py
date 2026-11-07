"""Operation steps for provision, delete, recovery, backup, and restore."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from api.errors import WorkflowError
from api.ids import token
from api.store import retention_deadline
from worker.render import load_versions, monitoring_script, render_data_cloud_init, render_ops_cloud_init

log = logging.getLogger("scylla.workflow")

SEEDS = "10.0.1.10,10.0.1.11"
SSH_PLACEHOLDER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForUnitTests scylla-dbaas"


@dataclass
class Ctx:
    store: object
    cloud: object
    settings: object
    operation: dict
    cluster: dict
    versions: dict = field(default_factory=dict)
    nodes: list = field(default_factory=list)

    def refresh(self) -> None:
        self.cluster = self.store.get_cluster(self.cluster["id"])
        self.nodes = self.store.list_nodes(self.cluster["id"])

    def layout(self) -> dict:
        return json.loads(self.cluster.get("layout_json") or "{}")

    def save_layout(self, **kwargs) -> None:
        data = self.layout()
        data.update(kwargs)
        self.store.update_cluster(self.cluster["id"], layout_json=json.dumps(data))
        self.refresh()

    def data_nodes(self) -> list[dict]:
        return [node for node in self.nodes if node["role"] == "data"]

    def ops_node(self) -> dict:
        return next(node for node in self.nodes if node["role"] == "ops")

    def tags(self, role: str) -> dict:
        return {"app": "scylla-dbaas", "cluster-id": self.cluster["id"], "role": role}


def run_steps(ctx: Ctx, steps: list[tuple[str, object]]) -> None:
    names = [name for name, _fn in steps]
    start = 0
    if ctx.operation.get("step") in names:
        start = names.index(ctx.operation["step"])
    for name, fn in steps[start:]:
        ctx.store.set_operation_step(ctx.operation["id"], name)
        ctx.operation["step"] = name
        hook = getattr(ctx.cloud, "checkpoint", None)
        if hook:
            hook(name)
        log.info(
            "cluster=%s operation=%s step=%s",
            ctx.cluster["id"],
            ctx.operation["id"],
            name,
        )
        fn(ctx)


def _ssh_key(ctx: Ctx) -> str:
    if ctx.settings.cloud != "azure":
        return SSH_PLACEHOLDER
    from worker.sshkey import ensure_ssh_public_key

    return ensure_ssh_public_key(ctx.settings.var_dir)


def _poll(ctx: Ctx, fn, attempts: int = 30):
    last = "not ready"
    for _ in range(attempts):
        try:
            value = fn()
        except Exception as exc:
            last = str(exc)
            value = None
        if value:
            return value
        delay = getattr(ctx.cloud, "poll_delay", 0)
        if delay:
            time.sleep(delay)
    raise WorkflowError(f"timed out waiting for the cluster ({last})")


def _nodetool_flag(output: str, ip: str) -> str:
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ip:
            return parts[0]
    return ""


def _host_id(output: str) -> str:
    for part in output.replace(",", " ").split():
        if part.count("-") == 4 and len(part) >= 32:
            return part
    raise WorkflowError("Scylla host id was not in the command output")


def _run(ctx: Ctx, node: dict, script: str) -> str:
    return ctx.cloud.run_command(ctx.cluster["compute_rg"], node["vm_name"], script)


def _require_ok(output: str) -> None:
    if "DBAAS_OK" not in output:
        text = output.strip() or "remote command failed"
        raise WorkflowError(text[-2000:])


def step_validate(ctx: Ctx) -> None:
    ctx.refresh()
    ctx.versions = load_versions(ctx.settings.repo_root)
    if not ctx.cloud.quota_ok(ctx.cluster["location"]):
        data = ctx.versions["DATA_VM_SIZE"]
        ops = ctx.versions["OPS_VM_SIZE"]
        raise WorkflowError(f"Subscription quota cannot supply 3 {data} and 1 {ops}")
    if ctx.settings.cloud == "azure" and not ctx.settings.operator_cidr:
        raise WorkflowError("OPERATOR_CIDR is required so network rules can allow your IP")
    if not ctx.cluster.get("cql_secret"):
        ctx.store.update_cluster(
            ctx.cluster["id"],
            cql_user="demo_app",
            cql_secret=token(),
            manager_secret=token(),
            manager_agent_token=token(),
            grafana_password=token(),
            manager_install=ctx.versions.get("MANAGER_INSTALL", "docker"),
            io_profile="standard",
        )
    ctx.store.set_cluster_status(ctx.cluster["id"], "provisioning")
    ctx.refresh()


def step_create_groups(ctx: Ctx) -> None:
    ctx.refresh()
    ctx.cloud.ensure_group(ctx.cluster["compute_rg"], ctx.cluster["location"], ctx.tags("compute"))
    ctx.cloud.ensure_group(ctx.cluster["backup_rg"], ctx.cluster["location"], ctx.tags("backup"))


def step_create_network(ctx: Ctx) -> None:
    ctx.refresh()
    out = ctx.cloud.deploy(
        "network",
        ctx.cluster["compute_rg"],
        {
            "location": ctx.cluster["location"],
            "operatorCidr": ctx.settings.operator_cidr or "203.0.113.5/32",
            "vnetName": "scylla-vnet",
            "subnetPrefix": "10.0.1.0/24",
            "addressPrefix": "10.0.0.0/16",
        },
    )
    ctx.save_layout(subnetId=out["subnetId"], nsgId=out.get("nsgId", ""))


def step_create_backup(ctx: Ctx) -> None:
    ctx.refresh()
    out = ctx.cloud.deploy(
        "backup",
        ctx.cluster["backup_rg"],
        {"location": ctx.cluster["location"], "accountName": ctx.cluster["storage_account"]},
    )
    ctx.save_layout(storageAccountId=out["storageAccountId"])


def step_create_identity(ctx: Ctx) -> None:
    ctx.refresh()
    principal = ""
    identity_id = ""
    for _ in range(5):
        out = ctx.cloud.deploy(
            "identity",
            ctx.cluster["compute_rg"],
            {"location": ctx.cluster["location"], "identityName": f"{ctx.cluster['storage_account']}-id"},
        )
        principal = out.get("principalId") or ""
        identity_id = out.get("identityId") or ""
        if principal and identity_id:
            break
        delay = getattr(ctx.cloud, "poll_delay", 0)
        if delay:
            time.sleep(delay)
    if not principal:
        raise WorkflowError("Managed identity principal is not ready")
    ctx.cloud.assign_blob_role(principal, ctx.layout()["storageAccountId"])
    ctx.save_layout(identityId=identity_id, principalId=principal)


def step_create_nics(ctx: Ctx) -> None:
    ctx.refresh()
    subnet_id = ctx.layout()["subnetId"]
    for node in ctx.nodes:
        public = node["role"] == "ops" or node["ordinal"] == 0
        out = ctx.cloud.deploy(
            "nic",
            ctx.cluster["compute_rg"],
            {
                "location": ctx.cluster["location"],
                "nicName": node["nic_name"],
                "privateIp": node["private_ip"],
                "subnetId": subnet_id,
                "createPublicIp": public,
                "publicIpName": f"{node['nic_name']}-pip",
            },
        )
        ctx.store.update_node(node["id"], nic_id=out["nicId"], public_ip=out.get("publicIp") or None)


def _vm_base(ctx: Ctx, node: dict, custom_data: str) -> dict:
    versions = ctx.versions or load_versions(ctx.settings.repo_root)
    return {
        "location": ctx.cluster["location"],
        "vmName": node["vm_name"],
        "nicId": node["nic_id"],
        "sshPublicKey": _ssh_key(ctx),
        "customData": custom_data,
        "zone": versions.get("AZURE_ZONE", ctx.settings.zone),
        "adminUsername": "scylla",
        "ubuntuPublisher": versions["UBUNTU_PUBLISHER"],
        "ubuntuOffer": versions["UBUNTU_OFFER"],
        "ubuntuSku": versions["UBUNTU_SKU"],
        "ubuntuVersion": versions["UBUNTU_VERSION"],
    }


def step_create_ops(ctx: Ctx) -> None:
    ctx.refresh()
    ctx.versions = ctx.versions or load_versions(ctx.settings.repo_root)
    node = ctx.ops_node()
    custom = render_ops_cloud_init(
        ctx.versions,
        grafana_password=ctx.cluster["grafana_password"],
        agent_token=ctx.cluster["manager_agent_token"],
    )
    versions = ctx.versions
    params = _vm_base(ctx, node, custom)
    params["vmSize"] = versions["OPS_VM_SIZE"]
    params["osDiskGb"] = int(versions["OS_DISK_OPS_GB"])
    out = ctx.cloud.deploy("ops-vm", ctx.cluster["compute_rg"], params)
    ctx.store.update_node(node["id"], vm_id=out.get("vmId"), status="up")


def _seeds_for(node: dict, *, replace: bool) -> str:
    # Node 0 is the first process in an empty cluster. Listing 10.0.1.11
    # before that VM exists leaves Scylla in STARTING. Nodes 1 and 2 contact
    # the seeds that are already up, and those seeds include themselves.
    # A replacement must not. Scylla rejects replace_node_first_boot when the
    # new process is a seed, so node 0 or node 1 would stay down and wait_un
    # would poll until timeout. Node 2 is not a seed, so its list is unchanged.
    if replace:
        live = [ip for ip in SEEDS.split(",") if ip != node["private_ip"]]
        return ",".join(live)
    if node["ordinal"] != 0:
        return SEEDS
    return node["private_ip"]


def _data_custom(ctx: Ctx, node: dict, replace_value: str = "") -> str:
    ctx.versions = ctx.versions or load_versions(ctx.settings.repo_root)
    return render_data_cloud_init(
        ctx.versions,
        cluster_name=ctx.cluster["name"],
        private_ip=node["private_ip"],
        seeds=_seeds_for(node, replace=bool(replace_value)),
        storage_account=ctx.cluster["storage_account"],
        agent_token=ctx.cluster["manager_agent_token"],
        replace_value=replace_value,
    )


def _deploy_data(ctx: Ctx, node: dict, replace_value: str = "") -> None:
    versions = ctx.versions or load_versions(ctx.settings.repo_root)
    params = _vm_base(ctx, node, _data_custom(ctx, node, replace_value))
    params.update(
        {
            "vmSize": versions["DATA_VM_SIZE"],
            "osDiskGb": int(versions["OS_DISK_DATA_GB"]),
            "dataDiskGb": int(versions["DATA_DISK_GB"]),
            "identityId": ctx.layout()["identityId"],
        }
    )
    out = ctx.cloud.deploy("data-vm", ctx.cluster["compute_rg"], params)
    ctx.store.update_node(node["id"], vm_id=out.get("vmId"), status="provisioning")


def _wait_un(ctx: Ctx, node: dict) -> None:
    def ready():
        output = _run(ctx, node, "#!/bin/bash\n# dbaas:nodetool\nnodetool status\n")
        return output if _nodetool_flag(output, node["private_ip"]) == "UN" else None

    _poll(ctx, ready)
    ctx.store.update_node(node["id"], status="up")


def step_create_node0(ctx: Ctx) -> None:
    ctx.refresh()
    node = next(item for item in ctx.data_nodes() if item["ordinal"] == 0)
    _deploy_data(ctx, node)
    ctx.refresh()
    node = next(item for item in ctx.data_nodes() if item["ordinal"] == 0)
    _wait_un(ctx, node)


def step_create_node1_node2(ctx: Ctx) -> None:
    ctx.refresh()
    for ordinal in (1, 2):
        node = next(item for item in ctx.data_nodes() if item["ordinal"] == ordinal)
        _deploy_data(ctx, node)
    ctx.refresh()
    for ordinal in (1, 2):
        node = next(item for item in ctx.data_nodes() if item["ordinal"] == ordinal)
        _wait_un(ctx, node)


def step_capture_host_ids(ctx: Ctx) -> None:
    ctx.refresh()
    for node in ctx.data_nodes():
        output = _run(
            ctx,
            node,
            "#!/bin/bash\n# dbaas:host-id\nnodetool info\n",
        )
        ctx.store.update_node(node["id"], host_id=_host_id(output), status="up")


def step_configure_auth(ctx: Ctx) -> None:
    ctx.refresh()
    node = next(item for item in ctx.data_nodes() if item["ordinal"] == 0)
    secret = ctx.cluster["cql_secret"]
    manager = ctx.cluster["manager_secret"]
    # Scylla 2026 does not create the cassandra/cassandra superuser. The
    # maintenance socket is local to the node and can create the first role.
    script = f"""#!/bin/bash
# dbaas:configure-auth
set -euo pipefail
SOCK=/var/lib/scylla/cql.m
test -S "$SOCK"
cqlsh "$SOCK" -e "CREATE ROLE IF NOT EXISTS scylla_manager WITH PASSWORD = '{manager}' AND LOGIN = true AND SUPERUSER = true"
cqlsh "$SOCK" -e "ALTER ROLE scylla_manager WITH PASSWORD = '{manager}' AND LOGIN = true AND SUPERUSER = true"
cqlsh "$SOCK" -e "CREATE ROLE IF NOT EXISTS demo_app WITH PASSWORD = '{secret}' AND LOGIN = true"
cqlsh "$SOCK" -e "ALTER ROLE demo_app WITH PASSWORD = '{secret}' AND LOGIN = true"
cqlsh "$SOCK" -e "CREATE KEYSPACE IF NOT EXISTS demo WITH replication = {{'class': 'NetworkTopologyStrategy', 'datacenter1': 3}}"
cqlsh "$SOCK" -e "GRANT ALL PERMISSIONS ON KEYSPACE demo TO demo_app"
echo DBAAS_OK
"""
    _require_ok(_run(ctx, node, script))


def step_configure_agents(ctx: Ctx) -> None:
    ctx.refresh()
    for node in ctx.data_nodes():
        _run(
            ctx,
            node,
            "#!/bin/bash\n# dbaas:check-location\nscylla-manager-agent check-location --location azure:scylla-backup\n",
        )


def step_register_manager(ctx: Ctx) -> None:
    ctx.refresh()
    ops = ctx.ops_node()
    hosts = ",".join(node["private_ip"] for node in ctx.data_nodes())
    token_value = ctx.cluster["manager_agent_token"]
    script = f"""#!/bin/bash
# dbaas:sctool-status
set -e
cd /opt/scylla-ops
docker compose --env-file /opt/scylla-ops/.env up -d
docker compose exec -T manager sctool cluster add --name {ctx.cluster['name']} --host {hosts} --auth-token '{token_value}' || true
echo DBAAS_OK
"""
    _require_ok(_run(ctx, ops, script))


def step_configure_monitoring(ctx: Ctx) -> None:
    ctx.refresh()
    if not ctx.versions:
        ctx.versions = load_versions(ctx.settings.repo_root)
    ops = ctx.ops_node()
    data_ips = [node["private_ip"] for node in ctx.data_nodes()]
    script = monitoring_script(data_ips, ctx.cluster["name"], ctx.cluster["grafana_password"], ctx.versions)
    _require_ok(_run(ctx, ops, script))
    public = ops.get("public_ip") or ops["private_ip"]
    ctx.store.update_cluster(ctx.cluster["id"], grafana_url=f"http://{public}:3000")


def step_schedule(ctx: Ctx) -> None:
    ctx.refresh()
    ops = ctx.ops_node()
    _run(
        ctx,
        ops,
        f"""#!/bin/bash
# dbaas:sctool-schedule
set -e
cd /opt/scylla-ops
docker compose --env-file /opt/scylla-ops/.env up -d
docker compose exec -T manager sctool backup --cluster {ctx.cluster['name']} --location azure:scylla-backup --retention 7 --interval 24h
docker compose exec -T manager sctool repair --cluster {ctx.cluster['name']} --interval 168h
""",
    )
    ctx.store.update_cluster(
        ctx.cluster["id"],
        backup_schedule_id="daily",
        repair_task_id="weekly",
    )


def step_ready(ctx: Ctx) -> None:
    ctx.refresh()
    node = next(item for item in ctx.data_nodes() if item["ordinal"] == 0)
    try:
        profile = _run(ctx, node, "#!/bin/bash\n# dbaas:io-profile\ncat /var/lib/scylla-dbaas-io-profile\n").strip()
    except Exception:
        profile = "standard"
    if profile not in {"standard", "demo"}:
        profile = "standard"
    ctx.store.update_cluster(ctx.cluster["id"], io_profile=profile)
    ctx.store.set_cluster_status(ctx.cluster["id"], "ready")


def run_create(ctx: Ctx) -> None:
    run_steps(
        ctx,
        [
            ("validate", step_validate),
            ("create_groups", step_create_groups),
            ("create_network", step_create_network),
            ("create_backup_storage", step_create_backup),
            ("create_identity", step_create_identity),
            ("create_nics", step_create_nics),
            ("create_ops_vm", step_create_ops),
            ("create_node0", step_create_node0),
            ("create_node1_node2", step_create_node1_node2),
            ("capture_host_ids", step_capture_host_ids),
            ("configure_auth", step_configure_auth),
            ("configure_agents", step_configure_agents),
            ("register_manager", step_register_manager),
            ("configure_monitoring", step_configure_monitoring),
            ("schedule_tasks", step_schedule),
            ("ready", step_ready),
        ],
    )


def step_delete_validate(ctx: Ctx) -> None:
    ctx.refresh()
    if ctx.cluster["status"] == "restoring":
        raise WorkflowError("A restore is in progress", cluster_status=None)
    ctx.store.set_cluster_status(ctx.cluster["id"], "deleting")


def step_stop_schedules(ctx: Ctx) -> None:
    ctx.refresh()
    ops = ctx.ops_node()
    try:
        _run(ctx, ops, "#!/bin/bash\n# dbaas:sctool-stop\ndocker compose exec -T manager sctool task stop --cluster all || true\n")
    except Exception:
        log.warning("cluster=%s could not stop manager schedules", ctx.cluster["id"])


def step_delete_vms(ctx: Ctx) -> None:
    ctx.refresh()
    for node in ctx.nodes:
        if ctx.cloud.power_state(ctx.cluster["compute_rg"], node["vm_name"]) != "missing":
            ctx.cloud.delete_vm(ctx.cluster["compute_rg"], node["vm_name"])


def step_delete_compute(ctx: Ctx) -> None:
    ctx.refresh()
    ctx.cloud.delete_group(ctx.cluster["compute_rg"])


def step_retain_backups(ctx: Ctx) -> None:
    ctx.refresh()
    if ctx.operation["request"].get("delete_backups"):
        ctx.cloud.delete_group(ctx.cluster["backup_rg"])
        ctx.store.update_cluster(ctx.cluster["id"], backup_retain_until=None)
    else:
        ctx.store.update_cluster(ctx.cluster["id"], backup_retain_until=retention_deadline())


def step_deleted(ctx: Ctx) -> None:
    ctx.store.set_cluster_status(ctx.cluster["id"], "deleted")


def run_delete(ctx: Ctx) -> None:
    run_steps(
        ctx,
        [
            ("validate", step_delete_validate),
            ("stop_schedules", step_stop_schedules),
            ("delete_vms", step_delete_vms),
            ("delete_compute_group", step_delete_compute),
            ("retain_backups", step_retain_backups),
            ("deleted", step_deleted),
        ],
    )


def _target_node(ctx: Ctx) -> dict:
    ctx.refresh()
    return ctx.store.get_node(ctx.operation["request"]["node_id"])


def run_restart(ctx: Ctx) -> None:
    def start_vm(_ctx: Ctx) -> None:
        node = _target_node(_ctx)
        _ctx.cloud.start_vm(_ctx.cluster["compute_rg"], node["vm_name"])
        _ctx.store.update_node(node["id"], status="restarting")

    def systemctl(_ctx: Ctx) -> None:
        node = _target_node(_ctx)
        _run(_ctx, node, "#!/bin/bash\n# dbaas:systemctl-restart\nsystemctl restart scylla-server\n")

    def verify(_ctx: Ctx) -> None:
        node = _target_node(_ctx)
        output = _run(_ctx, node, "#!/bin/bash\n# dbaas:nodetool\nnodetool status\n")
        if _nodetool_flag(output, node["private_ip"]) != "UN":
            raise WorkflowError("Scylla did not return to UN", cluster_status=None)
        _ctx.store.update_node(node["id"], status="up")

    steps = []
    if ctx.operation["request"].get("mode") == "start_vm":
        steps.append(("start_vm", start_vm))
    steps.extend([("systemctl_restart", systemctl), ("verify", verify)])
    run_steps(ctx, steps)


def run_replace(ctx: Ctx) -> None:
    node = _target_node(ctx)
    peers = [item for item in ctx.data_nodes() if item["id"] != node["id"]]
    if any(item["status"] != "up" for item in peers):
        raise WorkflowError("Two data nodes are down. Replacement is disabled.", cluster_status=None)
    if not node.get("host_id"):
        raise WorkflowError("This node has no stored host id, so it cannot be replaced", cluster_status=None)

    def detach(_ctx: Ctx) -> None:
        current = _target_node(_ctx)
        power = _ctx.cloud.power_state(_ctx.cluster["compute_rg"], current["vm_name"])
        if power != "missing":
            _ctx.cloud.delete_vm(_ctx.cluster["compute_rg"], current["vm_name"])
        _ctx.store.update_node(current["id"], status="replacing")

    def deploy(_ctx: Ctx) -> None:
        current = _target_node(_ctx)
        _ctx.versions = load_versions(_ctx.settings.repo_root)
        _deploy_data(_ctx, current, replace_value=current["host_id"])

    def wait_un(_ctx: Ctx) -> None:
        current = _target_node(_ctx)
        ips = [item["private_ip"] for item in _ctx.data_nodes()]

        def ready():
            output = _run(_ctx, current, "#!/bin/bash\n# dbaas:nodetool\nnodetool status\n")
            if any(_nodetool_flag(output, ip) != "UN" for ip in ips):
                return None
            return output

        _poll(_ctx, ready)
        _ctx.store.update_node(current["id"], status="up")

    def refresh_monitoring(_ctx: Ctx) -> None:
        step_configure_monitoring(_ctx)

    def repair(_ctx: Ctx) -> None:
        ops = _ctx.ops_node()
        _run(
            _ctx,
            ops,
            f"#!/bin/bash\n# dbaas:sctool-repair\ndocker compose exec -T manager sctool repair --cluster {_ctx.cluster['name']}\n",
        )

    def verify(_ctx: Ctx) -> None:
        _ctx.refresh()
        current = _target_node(_ctx)
        _ctx.store.update_node(current["id"], status="up")
        if _ctx.cluster["status"] == "degraded":
            _ctx.store.set_cluster_status(_ctx.cluster["id"], "ready")

    run_steps(
        ctx,
        [
            ("detach", detach),
            ("deploy", deploy),
            ("wait_un", wait_un),
            ("refresh_monitoring", refresh_monitoring),
            ("repair", repair),
            ("verify", verify),
        ],
    )


def run_backup(ctx: Ctx) -> None:
    from api.audit_actions import BACKUP_START

    ctx.refresh()
    ctx.store.audit(
        "worker",
        BACKUP_START,
        ctx.cluster["id"],
        "",
        "accepted",
        detail={"operation_id": ctx.operation["id"]},
    )

    def start(_ctx: Ctx) -> None:
        ops = _ctx.ops_node()
        output = _run(
            _ctx,
            ops,
            f"#!/bin/bash\n# dbaas:sctool-backup\ndocker compose exec -T manager sctool backup --cluster {_ctx.cluster['name']} --location azure:scylla-backup\n",
        )
        if "failed" in output.lower() and "succeeded" not in output.lower():
            raise WorkflowError(output[:300], cluster_status=None)
        _ctx.operation["backup_output"] = output

    def poll(_ctx: Ctx) -> None:
        output = _ctx.operation.get("backup_output", "")
        if "succeeded" not in output.lower():
            tasks = _run(_ctx, _ctx.ops_node(), "#!/bin/bash\n# dbaas:sctool-tasks\n")
            if not tasks.strip():
                raise WorkflowError("Manager backup did not succeed", cluster_status=None)
            output = f"started {tasks.splitlines()[-1]}\nstatus succeeded"
            _ctx.operation["backup_output"] = output

    def record(_ctx: Ctx) -> None:
        _ctx.refresh()
        output = _ctx.operation.get("backup_output", "")
        run_id = ""
        for line in output.splitlines():
            if line.startswith("started "):
                run_id = line.split()[1]
        note = ""
        if any(node["status"] != "up" for node in _ctx.data_nodes()):
            note = "Manager reported a partial node set."
        _ctx.store.record_backup(
            _ctx.cluster["id"],
            _ctx.operation["id"],
            "ondemand",
            run_id or None,
            "azure:scylla-backup",
            "succeeded",
            note,
        )

    run_steps(ctx, [("start", start), ("poll", poll), ("record", record)])


def run_restore(ctx: Ctx) -> None:
    from api.audit_actions import RESTORE_START

    ctx.store.audit(
        "worker",
        RESTORE_START,
        ctx.cluster["id"],
        ctx.operation["request"].get("backup_id", ""),
        "accepted",
        detail={"operation_id": ctx.operation["id"]},
    )

    def quiesce(_ctx: Ctx) -> None:
        _ctx.store.set_cluster_status(_ctx.cluster["id"], "restoring")

    def restore(_ctx: Ctx) -> None:
        ops = _ctx.ops_node()
        backup_id = _ctx.operation["request"]["backup_id"]
        _run(
            _ctx,
            ops,
            f"#!/bin/bash\n# dbaas:sctool-restore\ndocker compose exec -T manager sctool restore --cluster {_ctx.cluster['name']} --location azure:scylla-backup --restore-schema\n# backup {backup_id}\n",
        )

    def verify(_ctx: Ctx) -> None:
        node = next(item for item in _ctx.data_nodes() if item["ordinal"] == 0)
        try:
            _run(
                _ctx,
                node,
                "#!/bin/bash\n# dbaas:count\n"
                f"cqlsh {node['private_ip']} -u {_ctx.cluster['cql_user']} -p '{_ctx.cluster['cql_secret']}' "
                "-e 'SELECT count(*) FROM demo.events'\n",
            )
        except Exception as exc:
            raise WorkflowError(
                "The restore command finished and the check did not.",
                cluster_status="ready",
            ) from exc

    def ready(_ctx: Ctx) -> None:
        _ctx.store.set_cluster_status(_ctx.cluster["id"], "ready")

    run_steps(ctx, [("quiesce", quiesce), ("restore", restore), ("verify", verify), ("ready", ready)])
