# Operator runbook

The short path is [Getting started](../getting-started.md): `docker compose up` for the fake cloud, and `docker compose --profile azure up azure` for a real subscription. This page is the live-cluster detail after that UI is up.

## Fake cloud

```bash
export API_KEY=dev
export CLOUD=fake
uvicorn api.main:app
```

1. Open http://127.0.0.1:8000 and save the API key.
2. Create cluster `demo` in `eastus`.
3. The operation step changes on the cluster page until `ready`. On the fake cloud this takes one worker tick, about two seconds.
4. The CQL block shows `demo_app` and a password. The host is a documentation address (`203.0.113.10`), not a real VM.

## Azure

```bash
export API_KEY=dev
export CLOUD=azure
export OPERATOR_CIDR="$(curl -fsS https://api.ipify.org)/32"
export AZURE_TENANT_ID=directory-tenant-id
export AZURE_CLIENT_ID=application-client-id
export AZURE_CLIENT_SECRET=client-secret-value
export AZURE_SUBSCRIPTION_ID=subscription-id
uvicorn api.main:app
```

The process runs `az login --service-principal` on the first Azure command. The portal steps for those four values are in [Getting started](../getting-started.md).

Create `demo` in `eastus`. The first boot installs Scylla packages and is the slow part: expect several tens of minutes. Grafana is linked from the cluster page. The CQL client:

```bash
pip install 'cassandra-driver>=3.29'
python -m clients.cql_demo --host NODE0_PUBLIC_IP --username demo_app --password 'PASSWORD_FROM_THE_PAGE'
```

If the UI is down and the VMs are still running, delete both groups:

```bash
az group list --tag app=scylla-dbaas --query "[].name" -o tsv
az group delete -n rg-scylla-SHORT --yes
az group delete -n rg-scylla-SHORT-bak --yes
```

`SHORT` is the suffix in the group name shown by the list command.

## What to do when create fails

The cluster page shows the step name and the error. On the VM, cloud-init output is:

```bash
az vm run-command invoke -g rg-scylla-SHORT -n scylla-SHORT-n0 \
  --command-id RunShellScript --scripts "tail -n 80 /var/log/cloud-init-output.log"
```

A failed create stays in `error`. Delete it from the UI (type the cluster name). Fix the cause, then create again. The single-cluster cap counts only clusters that are not `deleted`.

## Cost

Retail prices were not queried in this build, so this runbook does not invent a dollar figure. The demo size in `eastus` is 3× `Standard_D4ds_v5` with a 256 GiB Premium disk each, plus 1× `Standard_D4s_v5`, two public IPs, and one storage account. Price the SKU before the first create:

```bash
az vm list-skus -l eastus --size Standard_D4ds_v5 --query "[0].name" -o tsv
```

Stop the bill by deleting the cluster. Four running VMs are the cost.

## Versions

`versions.env` pins the Ubuntu image offer, the Scylla 2026.3 apt list, Manager 3.13, and `replace_node_first_boot`. Confirm the image with `az vm image list -p Canonical -f ubuntu-24_04-lts --all` before a live create. If the Manager container cannot register the cluster, set `MANAGER_INSTALL=package` and create again.
