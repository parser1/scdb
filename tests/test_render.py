"""Cloud-init, targets, and compose render checks."""

from pathlib import Path

import yaml

from api.settings import ROOT
from worker.render import (
    load_versions,
    monitoring_script,
    prometheus_config,
    render_data_cloud_init,
    render_ops_cloud_init,
    scylla_targets,
)


def test_versions_file_has_required_keys():
    versions = load_versions(ROOT)
    for key in (
        "SCYLLA_LIST_URL",
        "SCYLLA_REPLACE_FLAG",
        "UBUNTU_OFFER",
        "DATA_VM_SIZE",
        "MANAGER_INSTALL",
    ):
        assert versions[key]


def test_data_cloud_init_parses_and_hides_storage_keys():
    versions = load_versions(ROOT)
    rendered = render_data_cloud_init(
        versions,
        cluster_name="demo",
        private_ip="10.0.1.10",
        seeds="10.0.1.10,10.0.1.11",
        storage_account="stdemo",
        agent_token="token-value",
    )
    parsed = yaml.safe_load(rendered)
    assert parsed["runcmd"]
    assert "stdemo" in rendered
    assert "key:" not in rendered
    assert "replace_node_first_boot" not in rendered
    assert "api_address: 127.0.0.1" in rendered
    assert "maintenance_socket: /var/lib/scylla/cql.m" in rendered
    assert "/dev/disk/azure/data/by-lun/0" in rendered
    assert "--disks" in rendered
    assert "--no-raid-setup --" not in rendered
    assert "scylla_setup" in rendered and "--no-raid-setup" not in rendered.split("scylla_setup", 1)[1]
    assert "chown -R scylla:scylla /var/lib/scylla" in rendered
    assert "scylla-node-exporter" in rendered
    assert "seeds: \"10.0.1.10,10.0.1.11\"" in rendered
    replacing = render_data_cloud_init(
        versions,
        cluster_name="demo",
        private_ip="10.0.1.12",
        seeds="10.0.1.10,10.0.1.11",
        storage_account="stdemo",
        agent_token="token-value",
        replace_value="675ed9f4-6564-6dbd-ca08-43fddce952de",
    )
    assert "replace_node_first_boot: 675ed9f4-6564-6dbd-ca08-43fddce952de" in replacing
    yaml.safe_load(replacing)


def test_host_id_parser_reads_nodetool_info():
    from worker.flows import WorkflowError, _host_id

    output = "ID                     : d6e68326-5e76-46d2-8ea6-b78879ffbad1\nGossip active          : true\n"
    assert _host_id(output) == "d6e68326-5e76-46d2-8ea6-b78879ffbad1"
    try:
        _host_id("Connection error: Connection refused")
    except WorkflowError as exc:
        assert "host id" in str(exc)
    else:
        raise AssertionError("expected a missing host id to fail")


def test_node0_seeds_itself_until_peers_exist():
    from worker.flows import _seeds_for

    assert _seeds_for({"ordinal": 0, "private_ip": "10.0.1.10"}, replace=False) == "10.0.1.10"
    assert _seeds_for({"ordinal": 1, "private_ip": "10.0.1.11"}, replace=False) == "10.0.1.10,10.0.1.11"
    assert _seeds_for({"ordinal": 0, "private_ip": "10.0.1.10"}, replace=True) == "10.0.1.11"
    assert _seeds_for({"ordinal": 1, "private_ip": "10.0.1.11"}, replace=True) == "10.0.1.10"
    assert _seeds_for({"ordinal": 2, "private_ip": "10.0.1.12"}, replace=True) == "10.0.1.10,10.0.1.11"


def test_ops_cloud_init_includes_compose():
    import base64
    import re

    versions = load_versions(ROOT)
    rendered = render_ops_cloud_init(versions, grafana_password="pw", agent_token="tok")
    parsed = yaml.safe_load(rendered)
    assert parsed["runcmd"]
    encoded = re.search(r"echo '([A-Za-z0-9+/=]+)' \| base64 -d", rendered).group(1)
    assert "scylladb/scylla-manager" in base64.b64decode(encoded).decode()
    assert "--profile" not in rendered
    assert "|| true" not in rendered.split("git clone", 1)[1].split("\n", 1)[0]


def test_scylla_targets_golden():
    text = scylla_targets(["10.0.1.10", "10.0.1.11", "10.0.1.12"], "demo")
    expected = (ROOT / "tests" / "fixtures" / "scylla_servers.yml").read_text()
    assert text == expected
    config = prometheus_config(["10.0.1.10", "10.0.1.11", "10.0.1.12"], "10.0.1.4", "demo")
    loaded = yaml.safe_load(config)
    assert loaded["scrape_configs"][0]["static_configs"][0]["targets"][0] == "10.0.1.10:9180"


def test_example_parameters_have_no_secrets():
    text = (ROOT / "infra" / "bicep" / "parameters.example.json").read_text().lower()
    for word in ("password", "secret", "api_key", "accountkey"):
        assert word not in text


def test_monitoring_script_starts_scylla_stack():
    versions = load_versions(ROOT)
    script = monitoring_script(["10.0.1.10", "10.0.1.11", "10.0.1.12"], "demo", "pw", versions)
    assert "start-all.sh" in script
    assert "10.0.1.10:9180" in script
    assert "10.0.1.10:9100" in script
    assert versions["MONITORING_REF"] in script
    assert "DBAAS_OK" in script


def test_compose_matches_versions():
    versions = load_versions(ROOT)
    compose = Path("ops/compose/compose.yml").read_text()
    for key in ("SCYLLA_IMAGE", "MANAGER_IMAGE"):
        assert versions[key] in compose
    assert "prom/prometheus" not in compose
    assert "grafana/grafana" not in compose
