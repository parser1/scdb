"""Azure CLI adapter redacts scripts and reads quota."""

import json
import logging
import subprocess
from pathlib import Path

import pytest

from api.settings import ROOT
from worker.cloud import AzureCloud


@pytest.fixture(autouse=True)
def _clear_service_principal(monkeypatch):
    for name in (
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def _ok(stdout="{}"):
    def runner(args):
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return runner


def test_run_command_redacts_password(caplog):
    cloud = AzureCloud(ROOT, runner=_ok('{"value": [{"message": "ok"}]}'))
    with caplog.at_level(logging.INFO, logger="scylla.cloud"):
        output = cloud.run_command("rg", "vm", "PASSWORD=supersecret\n# dbaas:host-id\n")
    assert output == "ok"
    assert "supersecret" not in caplog.text
    recorded = cloud.recent_commands()
    assert recorded and recorded[-1]["ok"] is True
    assert "dbaas:host-id" in recorded[-1]["command"]
    assert "supersecret" not in recorded[-1]["command"]
    assert recorded[-1]["result"] == "ok"


def test_failed_az_command_is_recorded():
    def runner(args):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="the deployment failed")

    cloud = AzureCloud(ROOT, runner=runner)
    with pytest.raises(RuntimeError, match="deployment failed"):
        cloud.ensure_group("rg-demo", "eastus", {"app": "scylla-dbaas"})
    recorded = cloud.recent_commands()
    assert recorded[-1]["ok"] is False
    assert recorded[-1]["command"].startswith("az group create")
    assert "deployment failed" in recorded[-1]["result"]


def _usage_row(local_name: str, value: str, limit: int, current: int = 0) -> dict:
    return {
        "localName": local_name,
        "name": {"value": value, "localizedValue": local_name},
        "currentValue": current,
        "limit": limit,
    }


def test_quota_rejects_a_small_data_family():
    payload = json.dumps(
        [
            _usage_row("Standard Ddsv6 Family vCPUs", "standardDDsv6Family", limit=10),
            _usage_row("Standard Dsv6 Family vCPUs", "standardDSv6Family", limit=10),
        ]
    )
    cloud = AzureCloud(ROOT, runner=_ok(payload))
    assert cloud.quota_ok("eastus") is False


def test_quota_rejects_a_small_ops_family():
    payload = json.dumps(
        [
            _usage_row("Standard Ddsv6 Family vCPUs", "standardDDsv6Family", limit=16),
            _usage_row("Standard Dsv6 Family vCPUs", "standardDSv6Family", limit=2),
        ]
    )
    cloud = AzureCloud(ROOT, runner=_ok(payload))
    assert cloud.quota_ok("eastus") is False


def test_quota_allows_both_v6_families():
    payload = json.dumps(
        [
            _usage_row("Standard Ddsv6 Family vCPUs", "standardDDsv6Family", limit=12),
            _usage_row("Standard Dsv6 Family vCPUs", "standardDSv6Family", limit=4),
        ]
    )
    cloud = AzureCloud(ROOT, runner=_ok(payload))
    assert cloud.quota_ok("eastus") is True


def test_missing_resource_group_counts_as_already_gone():
    message = (
        "ERROR: (ResourceGroupNotFound) Resource group 'rg-scylla-20jxj4zt' could not be found. "
        "Code: ResourceGroupNotFound Message: Resource group 'rg-scylla-20jxj4zt' could not be found."
    )

    def runner(args):
        return subprocess.CompletedProcess(args, 3, stdout="", stderr=message)

    cloud = AzureCloud(ROOT, runner=runner)
    assert cloud.power_state("rg-scylla-20jxj4zt", "vm") == "missing"
    cloud.delete_vm("rg-scylla-20jxj4zt", "vm")
    cloud.delete_group("rg-scylla-20jxj4zt")


def test_other_azure_errors_still_fail_delete():
    def runner(args):
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="ERROR: (AuthorizationFailed) no access"
        )

    cloud = AzureCloud(ROOT, runner=runner)
    with pytest.raises(RuntimeError, match="AuthorizationFailed"):
        cloud.delete_group("rg-scylla-20jxj4zt")


def test_quota_allows_when_the_family_is_absent():
    cloud = AzureCloud(ROOT, runner=_ok("[]"))
    assert cloud.quota_ok("eastus") is True


def test_quota_reads_the_cli_shape_with_a_name_object():
    payload = json.dumps(
        [
            {
                "localName": "Standard Ddsv6 Family vCPUs",
                "name": {
                    "value": "standardDDsv6Family",
                    "localizedValue": "Standard Ddsv6 Family vCPUs",
                },
                "currentValue": 0,
                "limit": 10,
            },
            {
                "localName": "Standard Dsv6 Family vCPUs",
                "name": {
                    "value": "standardDSv6Family",
                    "localizedValue": "Standard Dsv6 Family vCPUs",
                },
                "currentValue": 0,
                "limit": 10,
            },
        ]
    )
    cloud = AzureCloud(ROOT, runner=_ok(payload))
    assert cloud.quota_ok("eastus") is False


def test_service_principal_login_once_and_secret_is_redacted(monkeypatch, caplog):
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-1")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-1")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "super-secret-value")
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    calls = []

    def runner(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    cloud = AzureCloud(ROOT, runner=runner)
    with caplog.at_level(logging.INFO, logger="scylla.cloud"):
        assert cloud.list_tagged_groups() == []
        assert cloud.list_tagged_groups() == []
    assert calls[0] == [
        "az",
        "login",
        "--service-principal",
        "-u",
        "client-1",
        "-p",
        "super-secret-value",
        "--tenant",
        "tenant-1",
    ]
    assert calls[1] == ["az", "account", "set", "--subscription", "sub-1"]
    assert calls[2][1] == "group"
    assert calls[3][1] == "group"
    assert len(calls) == 4
    assert "super-secret-value" not in caplog.text


def test_partial_service_principal_is_rejected(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "client-1")
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    cloud = AzureCloud(ROOT, runner=_ok("[]"))
    try:
        cloud.list_tagged_groups()
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected a missing-variable error")
    assert "AZURE_CLIENT_SECRET" in message
    assert "AZURE_TENANT_ID" in message
    assert "AZURE_SUBSCRIPTION_ID" in message


def test_deploy_wraps_parameter_values_for_the_cli():
    seen = {}

    def runner(args):
        path = next(arg[1:] for arg in args if arg.startswith("@"))
        seen["params"] = json.loads(Path(path).read_text())
        return subprocess.CompletedProcess(
            args, 0, stdout='{"subnetId": {"value": "subnet-1"}}', stderr=""
        )

    cloud = AzureCloud(ROOT, runner=runner)
    out = cloud.deploy(
        "network",
        "rg",
        {"location": "eastus", "createPublicIp": True, "osDiskGb": 32},
    )
    assert seen["params"] == {
        "location": {"value": "eastus"},
        "createPublicIp": {"value": True},
        "osDiskGb": {"value": 32},
    }
    assert out["subnetId"] == "subnet-1"


def test_login_skipped_without_service_principal(monkeypatch):
    for name in (
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    calls = []

    def runner(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    cloud = AzureCloud(ROOT, runner=runner)
    assert cloud.list_tagged_groups() == []
    assert calls == [
        [
            "az",
            "group",
            "list",
            "--tag",
            "app=scylla-dbaas",
            "--query",
            "[].name",
            "-o",
            "json",
        ]
    ]
