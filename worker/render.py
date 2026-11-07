"""Render cloud-init, Prometheus targets, and the ops compose inputs."""

from __future__ import annotations

import base64
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from api.settings import ROOT


def load_versions(repo_root: Path | None = None) -> dict[str, str]:
    path = (repo_root or ROOT) / "versions.env"
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env(folder: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(folder),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )


def render_data_cloud_init(
    versions: dict[str, str],
    *,
    cluster_name: str,
    private_ip: str,
    seeds: str,
    storage_account: str,
    agent_token: str,
    replace_value: str = "",
) -> str:
    template = _env(ROOT / "infra" / "cloud-init").get_template("data-node.yaml.j2")
    return template.render(
        cluster_name=cluster_name,
        private_ip=private_ip,
        seeds=seeds,
        storage_account=storage_account,
        agent_token=agent_token,
        replace_flag=versions["SCYLLA_REPLACE_FLAG"],
        replace_value=replace_value,
        scylla_list_url=versions["SCYLLA_LIST_URL"],
        manager_list_url=versions["MANAGER_LIST_URL"],
        gpg_key=versions["SCYLLA_GPG_KEY"],
        data_disk_gb=versions["DATA_DISK_GB"],
    )


def render_ops_cloud_init(versions: dict[str, str], *, grafana_password: str, agent_token: str) -> str:
    compose = (ROOT / "ops" / "compose" / "compose.yml").read_text()
    template = _env(ROOT / "infra" / "cloud-init").get_template("ops-node.yaml.j2")
    return template.render(
        compose_b64=base64.b64encode(compose.encode()).decode(),
        manager_install=versions.get("MANAGER_INSTALL", "docker"),
        grafana_password=grafana_password,
        agent_token=agent_token,
        monitoring_repo=versions["MONITORING_REPO"],
        monitoring_ref=versions["MONITORING_REF"],
        manager_list_url=versions["MANAGER_LIST_URL"],
        scylla_list_url=versions["SCYLLA_LIST_URL"],
        gpg_key=versions["SCYLLA_GPG_KEY"],
    )


def scylla_targets(ips: list[str], cluster_name: str) -> str:
    return _static_targets(ips, 9180, cluster_name, "scylla")


def node_exporter_targets(ips: list[str], cluster_name: str) -> str:
    return _static_targets(ips, 9100, cluster_name, "node")


def _static_targets(ips: list[str], port: int, cluster_name: str, job: str) -> str:
    document = [
        {
            "targets": [f"{ip}:{port}" for ip in ips],
            "labels": {"cluster": cluster_name, "job": job},
        }
    ]
    return yaml.safe_dump(document, sort_keys=False)


def monitoring_script(
    data_ips: list[str],
    cluster_name: str,
    grafana_password: str,
    versions: dict[str, str],
) -> str:
    scylla = scylla_targets(data_ips, cluster_name)
    nodes = node_exporter_targets(data_ips, cluster_name)
    password = grafana_password.replace("'", "'\"'\"'")
    return f"""#!/bin/bash
# dbaas:monitoring
set -euo pipefail
if [ ! -x /opt/scylla-monitoring/start-all.sh ]; then
  rm -rf /opt/scylla-monitoring
  git clone --depth 1 --branch {versions["MONITORING_REF"]} {versions["MONITORING_REPO"]} /opt/scylla-monitoring
fi
install -d -m 0777 /var/lib/scylla-monitoring
cat > /opt/scylla-monitoring/prometheus/scylla_servers.yml <<'EOF'
{scylla}EOF
cat > /opt/scylla-monitoring/prometheus/node_exporter_servers.yml <<'EOF'
{nodes}EOF
cd /opt/scylla-monitoring
./start-all.sh \\
  -d /var/lib/scylla-monitoring \\
  -v {versions["MONITORING_SCYLLA_VERSION"]} \\
  -M {versions["MONITORING_MANAGER_VERSION"]} \\
  -a '{password}' \\
  -s /opt/scylla-monitoring/prometheus/scylla_servers.yml \\
  -n /opt/scylla-monitoring/prometheus/node_exporter_servers.yml
curl -fsS --retry 12 --retry-delay 5 --retry-all-errors http://127.0.0.1:3000/ >/dev/null
echo DBAAS_OK
"""


def prometheus_config(data_ips: list[str], manager_ip: str, cluster_name: str) -> str:
    document = {
        "global": {"scrape_interval": "30s"},
        "scrape_configs": [
            {
                "job_name": "scylla",
                "static_configs": yaml.safe_load(scylla_targets(data_ips, cluster_name)),
            },
            {
                "job_name": "node",
                "static_configs": [
                    {
                        "targets": [f"{ip}:9100" for ip in data_ips],
                        "labels": {"cluster": cluster_name},
                    }
                ],
            },
            {
                "job_name": "scylla-manager",
                "static_configs": [
                    {"targets": [f"{manager_ip}:5090"], "labels": {"cluster": cluster_name}}
                ],
            },
        ],
    }
    return yaml.safe_dump(document, sort_keys=False)
