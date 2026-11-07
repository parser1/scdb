"""Cloud port, in-memory fake, and Azure CLI adapter."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from api.ids import redact_text
from worker.render import load_versions

log = logging.getLogger("scylla.cloud")


def _usage_label(item: dict) -> str:
    """Display name from `az vm list-usage`.

    The CLI returns localName as a string and name as {value, localizedValue}.
    Older payloads omit one of the two.
    """
    local = item.get("localName")
    if isinstance(local, str) and local.strip():
        return local
    name = item.get("name")
    if isinstance(name, dict):
        return str(name.get("localizedValue") or name.get("value") or "")
    if isinstance(local, dict):
        return str(local.get("localizedValue") or local.get("value") or "")
    return str(local or name or "")


_USAGE_FAMILY = re.compile(r"standard\s*([a-z0-9]+?)\s*family", re.IGNORECASE)
_VM_SIZE = re.compile(r"^Standard_([A-Za-z])(\d+)([A-Za-z]*)_v(\d+)$")
_SECRET_FLAG = re.compile(r"(--auth-token|-a|-p)\s+(?:'[^']*'|\S+)")


def _resource_missing(message: str) -> bool:
    """True when Azure reports the group or resource is already gone."""
    text = message.lower()
    return (
        "resourcegroupnotfound" in text
        or "resourcenotfound" in text
        or "could not be found" in text
        or "not found" in text
    )


def _family_token(label: str) -> str:
    """Family id from a usage row, such as ddsv6 or dsv6.

    Ddsv6 contains dsv6 as a substring, so the token has to be exact.
    """
    match = _USAGE_FAMILY.search(label)
    return match.group(1).lower() if match else ""


def _size_need(size: str, count: int) -> tuple[str, int]:
    """Map Standard_D4ds_v6 to the Ddsv6 family and its total vCPUs."""
    match = _VM_SIZE.match(size.strip())
    if not match:
        raise ValueError(f"unrecognized VM size {size}")
    letter, cores, features, generation = match.groups()
    family = f"{letter}{features}v{generation}".lower()
    return family, count * int(cores)


class FakeCloud:
    def __init__(self):
        self.groups: set[str] = set()
        self.extra_groups: list[str] = []
        self.deployments: list[dict] = []
        self.commands: list[dict] = []
        self.power: dict[str, str] = {}
        self.node_states: dict[str, str] = {}
        self.host_ids: dict[str, str] = {}
        self.deleted_vms: list[str] = []
        self.role_assignments: list[dict] = []
        self.manager_runs: list[str] = []
        self.count_result = "1"
        self.count_error = False
        self.restart_fails = False
        self.quota = True
        self.io_profile = "standard"
        self.fail_steps: set[str] = set()
        self.stop_after: str | None = None
        self.stopped: set[str] = set()
        self.poll_delay = 0
        self.force_deep = True
        self.health_ticks = 0

    def checkpoint(self, step: str) -> None:
        from api.errors import Pause

        if step in self.fail_steps:
            self.fail_steps.remove(step)
            raise RuntimeError(f"injected failure at {step}")
        if self.stop_after == step and step not in self.stopped:
            self.stopped.add(step)
            raise Pause()

    def ensure_group(self, name: str, location: str, tags: dict) -> None:
        self.groups.add(name)

    def deploy(self, module: str, resource_group: str, params: dict) -> dict:
        self.checkpoint(f"deploy:{module}")
        self.deployments.append({"module": module, "resource_group": resource_group, "params": params})
        if module == "network":
            return {"subnetId": "/subnets/cluster", "nsgId": "/nsgs/cluster"}
        if module == "backup":
            return {
                "storageAccountId": f"/subscriptions/demo/storage/{params['accountName']}",
                "container": "scylla-backup",
            }
        if module == "identity":
            return {"identityId": "/identities/backup", "principalId": "principal-backup"}
        if module == "nic":
            public = ""
            if params.get("createPublicIp"):
                public = "203.0.113.10" if params["privateIp"] == "10.0.1.10" else "203.0.113.4"
            return {"nicId": f"/nics/{params['nicName']}", "publicIp": public}
        if module in {"data-vm", "ops-vm"}:
            self.power[params["vmName"]] = "running"
            self.node_states[params["vmName"]] = "UN"
            return {"vmId": f"/vms/{params['vmName']}"}
        return {}

    def delete_group(self, name: str) -> None:
        self.groups.discard(name)
        self.extra_groups = [group for group in self.extra_groups if group != name]

    def power_state(self, resource_group: str, vm_name: str) -> str:
        return self.power.get(vm_name, "missing")

    def start_vm(self, resource_group: str, vm_name: str) -> None:
        self.power[vm_name] = "running"

    def delete_vm(self, resource_group: str, vm_name: str) -> None:
        self.deleted_vms.append(vm_name)
        self.power[vm_name] = "missing"

    def assign_blob_role(self, principal_id: str, storage_account_id: str) -> None:
        self.role_assignments.append({"principal_id": principal_id, "scope": storage_account_id})

    def quota_ok(self, location: str) -> bool:
        return self.quota

    def list_tagged_groups(self) -> list[str]:
        return sorted(self.groups | set(self.extra_groups))

    def run_command(self, resource_group: str, vm_name: str, script: str) -> str:
        self.commands.append({"vm": vm_name, "script": script})
        if "dbaas:nodetool" in script:
            return self._nodetool(vm_name)
        if "dbaas:host-id" in script:
            import uuid

            return self.host_ids.setdefault(vm_name, str(uuid.uuid5(uuid.NAMESPACE_DNS, vm_name)))
        if "dbaas:io-profile" in script:
            return self.io_profile
        if "dbaas:check-location" in script:
            return ""
        if "dbaas:systemctl-restart" in script:
            if self.restart_fails:
                raise RuntimeError("scylla-server failed to start")
            self.node_states[vm_name] = "UN"
            return "active"
        if "dbaas:count" in script:
            if self.count_error:
                raise RuntimeError("select count failed")
            return self.count_result
        if "dbaas:sctool-backup" in script:
            run_id = f"run-{len(self.manager_runs) + 1}"
            self.manager_runs.append(run_id)
            return f"started {run_id}\nstatus succeeded"
        if "dbaas:sctool-tasks" in script:
            return "\n".join(self.manager_runs)
        if "dbaas:sctool-restore" in script:
            return "restore succeeded"
        if "dbaas:sctool-repair" in script:
            return "repair finished"
        if "dbaas:sctool-status" in script:
            return "3 UN\nDBAAS_OK"
        if "dbaas:monitoring" in script:
            return "DBAAS_OK"
        if "dbaas:configure-auth" in script:
            return "DBAAS_OK"
        if "dbaas:logs" in script:
            return f"logs {vm_name}\n{script}"
        return "ok"

    def _nodetool(self, vm_name: str) -> str:
        lines = []
        seen = set(self.power) | set(self.node_states) | {vm_name}
        for name in sorted(seen):
            if not name.startswith("scylla-") or name.endswith("-ops"):
                continue
            state = self.node_states.get(name, "UN")
            if self.power.get(name) not in {None, "running"}:
                state = "DN"
            flag = "UN" if state == "UN" else "DN"
            ip = _ip_for(name)
            host = self.host_ids.get(name, f"host-{name}")
            lines.append(f"{flag}  {ip}  1 KB  256  33%  {host}  rack1")
        if not lines:
            ip = _ip_for(vm_name)
            lines.append(f"UN  {ip}  1 KB  256  33%  host-{vm_name}  rack1")
        return "\n".join(lines)


def _ip_for(vm_name: str) -> str:
    if vm_name.endswith("-n0"):
        return "10.0.1.10"
    if vm_name.endswith("-n1"):
        return "10.0.1.11"
    if vm_name.endswith("-n2"):
        return "10.0.1.12"
    if vm_name.endswith("-ops"):
        return "10.0.1.4"
    return "10.0.1.10"


class AzureCloud:
    """Drives a subscription through the Azure CLI.

    When AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and AZURE_TENANT_ID are set,
    the first command signs in with that service principal. Otherwise the
    current Azure CLI session is used.
    """

    def __init__(self, repo_root: Path, runner=None):
        self.repo_root = Path(repo_root)
        self.runner = runner or _subprocess_runner
        self.poll_delay = 20
        self.force_deep = False
        self.health_ticks = 0
        self._session_ready = False
        self._log_lock = threading.Lock()
        self.command_log: list[dict] = []

    def ensure_login(self) -> None:
        if self._session_ready:
            return
        client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
        client_secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()
        tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
        subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
        if not client_id and not client_secret and not tenant_id:
            self._session_ready = True
            return
        missing = [
            name
            for name, value in (
                ("AZURE_TENANT_ID", tenant_id),
                ("AZURE_CLIENT_ID", client_id),
                ("AZURE_CLIENT_SECRET", client_secret),
                ("AZURE_SUBSCRIPTION_ID", subscription_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Set " + ", ".join(missing) + " before starting the Azure control plane."
            )
        self._invoke(
            [
                "login",
                "--service-principal",
                "-u",
                client_id,
                "-p",
                client_secret,
                "--tenant",
                tenant_id,
            ],
            secrets=[client_secret],
        )
        self._invoke(["account", "set", "--subscription", subscription_id])
        self._session_ready = True

    def ensure_group(self, name: str, location: str, tags: dict) -> None:
        args = ["group", "create", "-n", name, "-l", location, "--tags"]
        args.extend(f"{key}={value}" for key, value in tags.items())
        self._az(args)

    def deploy(self, module: str, resource_group: str, params: dict) -> dict:
        template = self.repo_root / "infra" / "bicep" / f"{module}.bicep"
        if not template.exists():
            raise RuntimeError(f"missing bicep module {template}")
        secrets = [str(value) for key, value in params.items() if "secret" in key or "token" in key or key == "customData"]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            # Current Azure CLI deserializes @file as dict[str, DeploymentParameter].
            # A bare string value fails with "Unable to build a model".
            json.dump({key: {"value": value} for key, value in params.items()}, handle)
            param_path = handle.name
        try:
            stdout = self._az(
                [
                    "deployment",
                    "group",
                    "create",
                    "-g",
                    resource_group,
                    "-f",
                    str(template),
                    "-p",
                    f"@{param_path}",
                    "--query",
                    "properties.outputs",
                    "-o",
                    "json",
                ],
                secrets=secrets,
            )
        finally:
            Path(param_path).unlink(missing_ok=True)
        if not stdout.strip():
            return {}
        raw = json.loads(stdout)
        return {key: value.get("value") for key, value in raw.items()}

    def delete_group(self, name: str) -> None:
        try:
            self._az(["group", "delete", "-n", name, "--yes"])
        except RuntimeError as exc:
            if _resource_missing(str(exc)):
                log.info("resource group %s is already gone", name)
                return
            raise

    def power_state(self, resource_group: str, vm_name: str) -> str:
        try:
            stdout = self._az(
                [
                    "vm",
                    "get-instance-view",
                    "-g",
                    resource_group,
                    "-n",
                    vm_name,
                    "--query",
                    "instanceView.statuses[?starts_with(code, 'PowerState/')].code",
                    "-o",
                    "tsv",
                ]
            )
        except RuntimeError as exc:
            if _resource_missing(str(exc)):
                return "missing"
            raise
        code = stdout.strip().splitlines()[-1] if stdout.strip() else ""
        mapping = {
            "PowerState/running": "running",
            "PowerState/stopped": "stopped",
            "PowerState/deallocated": "deallocated",
            "PowerState/starting": "starting",
        }
        return mapping.get(code, "missing" if not code else code)

    def start_vm(self, resource_group: str, vm_name: str) -> None:
        self._az(["vm", "start", "-g", resource_group, "-n", vm_name])

    def delete_vm(self, resource_group: str, vm_name: str) -> None:
        try:
            self._az(["vm", "delete", "-g", resource_group, "-n", vm_name, "--yes"])
        except RuntimeError as exc:
            if _resource_missing(str(exc)):
                log.info("vm %s in %s is already gone", vm_name, resource_group)
                return
            raise

    def assign_blob_role(self, principal_id: str, storage_account_id: str) -> None:
        self._az(
            [
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                principal_id,
                "--assignee-principal-type",
                "ServicePrincipal",
                "--role",
                "Storage Blob Data Contributor",
                "--scope",
                storage_account_id,
            ]
        )

    def quota_ok(self, location: str) -> bool:
        try:
            stdout = self._az(["vm", "list-usage", "-l", location, "-o", "json"])
        except RuntimeError:
            log.warning("could not read VM quota in %s; continuing", location)
            return True
        versions = load_versions(self.repo_root)
        needed: dict[str, int] = {}
        for size, count in (
            (versions["DATA_VM_SIZE"], 3),
            (versions["OPS_VM_SIZE"], 1),
        ):
            family, cores = _size_need(size, count)
            needed[family] = needed.get(family, 0) + cores
        found: dict[str, int | None] = {family: None for family in needed}
        for item in json.loads(stdout or "[]"):
            family = _family_token(_usage_label(item))
            if family not in needed or found[family] is not None:
                continue
            limit = int(item.get("limit") or 0)
            current = int(item.get("currentValue") or 0)
            found[family] = limit - current
        for family, want in needed.items():
            have = found[family]
            if have is None:
                log.warning("VM family %s was not in the quota list for %s; continuing", family, location)
                continue
            if have < want:
                return False
        return True

    def list_tagged_groups(self) -> list[str]:
        stdout = self._az(
            ["group", "list", "--tag", "app=scylla-dbaas", "--query", "[].name", "-o", "json"]
        )
        return json.loads(stdout or "[]")

    def run_command(self, resource_group: str, vm_name: str, script: str) -> str:
        shown = (
            f"az vm run-command invoke -g {resource_group} -n {vm_name} --command-id RunShellScript\n"
            + _redact_script(script)
        )
        stdout = self._az(
            [
                "vm",
                "run-command",
                "invoke",
                "-g",
                resource_group,
                "-n",
                vm_name,
                "--command-id",
                "RunShellScript",
                "--scripts",
                script,
                "-o",
                "json",
            ],
            secrets=[script],
            shown=shown,
            record=False,
        )
        payload = json.loads(stdout or "{}")
        messages = payload.get("value") or []
        chunks = [item.get("message", "") for item in messages]
        text = "\n".join(chunks)
        self._record(shown, redact_text(text, [script]), ok=True)
        return text

    def recent_commands(self) -> list[dict]:
        with self._log_lock:
            return list(self.command_log)

    def _record(self, command: str, result: str, ok: bool) -> None:
        text = result.strip()
        if len(text) > 8000:
            text = text[-8000:]
        entry = {"command": command, "result": text, "ok": ok}
        with self._log_lock:
            self.command_log.append(entry)
            del self.command_log[:-80]

    def _az(
        self,
        args: list[str],
        secrets: list[str] | None = None,
        shown: str | None = None,
        record: bool = True,
    ) -> str:
        self.ensure_login()
        return self._invoke(args, secrets, shown, record)

    def _invoke(
        self,
        args: list[str],
        secrets: list[str] | None = None,
        shown: str | None = None,
        record: bool = True,
    ) -> str:
        logged = redact_text(" ".join(args), secrets)
        log.info("az %s", logged)
        visible = shown or f"az {logged}"
        proc = self.runner(["az", *args])
        if proc.returncode != 0:
            message = redact_text(proc.stderr or proc.stdout or "az command failed", secrets)
            self._record(visible, message, ok=False)
            raise RuntimeError(message.strip())
        stdout = proc.stdout or ""
        if record:
            self._record(visible, redact_text(stdout, secrets), ok=True)
        return stdout


def _redact_script(script: str) -> str:
    return _SECRET_FLAG.sub(lambda match: match.group(1) + " <redacted>", redact_text(script))


def _subprocess_runner(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=False, capture_output=True, text=True)
