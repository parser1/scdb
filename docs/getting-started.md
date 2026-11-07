# Getting started

The control plane is a web UI and an API. Two Docker images run it:

| Image | What it does |
| --- | --- |
| `scylla-dbaas:fake` | Same screens and workflows, with no Azure account and no virtual machines |
| `scylla-dbaas:azure` | Creates the real 4-VM cluster in your subscription |

Both listen on http://127.0.0.1:8000. Run one at a time. They both publish port 8000.

You need Docker Engine and the Compose plugin (`docker compose version`).

## Fake cloud

From the repository root:

```bash
docker compose up --build
```

Wait until the log line says the application startup is complete, then open http://127.0.0.1:8000.

1. In the header, type API key `dev` and press Save. Override it at startup with `API_KEY=something docker compose up`.
2. Create a cluster named `demo` in `eastus`.
3. Open the cluster page. The operation step advances to `ready` in a few seconds.
4. The CQL block shows user `demo_app` and a password. The host `203.0.113.10` is a placeholder. Nothing is listening there.

Stop it with Ctrl-C, or from another terminal:

```bash
docker compose down
```

`docker compose down` keeps the catalog volume, so the next start still shows the cluster. `docker compose down -v` deletes that local catalog. There are no Azure resources to delete in this mode.

## Azure

This mode creates billable virtual machines. The container signs in with a service principal from `.env`. It does not use `az login` or the Azure CLI profile on your machine.

Create the principal in the Azure portal:

1. Microsoft Entra ID → App registrations → New registration. Copy the Application (client) ID and the Directory (tenant) ID.
2. That app → Certificates & secrets → New client secret. Copy the secret value. The portal shows it only once.
3. Subscriptions → your subscription → Access control (IAM) → Add role assignment. Assign **Owner**, or **Contributor** plus **User Access Administrator**, to that app. Copy the subscription ID.

Copy `.env.example` to `.env` and fill it in:

```bash
API_KEY=dev
OPERATOR_CIDR=203.0.113.10/32
AZURE_TENANT_ID=directory-tenant-id
AZURE_CLIENT_ID=application-client-id
AZURE_CLIENT_SECRET=client-secret-value
AZURE_SUBSCRIPTION_ID=subscription-id
```

`OPERATOR_CIDR` is your current public IP plus `/32`. The network rules allow that address to reach CQL and Grafana. Set it in `.env` from the repository root:

```bash
# write your address into OPERATOR_CIDR in .env, then start the Azure image
curl -fsS https://api.ipify.org
docker compose --profile azure up --build azure
```

Stop the fake service first if it is still running (`docker compose down`). Then open http://127.0.0.1:8000, save the API key, and create `demo`.

The first `az` command inside the container runs `az login --service-principal` and selects `AZURE_SUBSCRIPTION_ID`. The CLI token cache is the Docker volume `azure-cli`, mounted at `/root/.azure`.

The first create installs ScyllaDB on Ubuntu 24.04. Expect several tens of minutes. The cluster page shows the current step. When status is `ready`:

- Open the Grafana link on that page.
- Run the CQL client from the host, using the host, user, and password shown on the page:

```bash
pip install 'cassandra-driver>=3.29'
python -m clients.cql_demo --host NODE0_PUBLIC_IP --username demo_app --password 'PASSWORD_FROM_THE_PAGE'
```

Delete the cluster from the UI when you are finished. Type the cluster name to confirm. That removes the compute resource group. The backup group stays for 7 days unless you check "Delete backups now".

`docker compose --profile azure down` stops the control plane only. It does not delete virtual machines. If the UI is already gone and the VMs are still there:

```bash
az group list --tag app=scylla-dbaas --query "[].name" -o tsv
az group delete -n rg-scylla-SHORT --yes
az group delete -n rg-scylla-SHORT-bak --yes
```

`SHORT` is the suffix printed by the list command.

A failed create stays in status `error` with the step name on the cluster page. Cloud-init output and the rest of the failure path are in [delivery/runbook.md](delivery/runbook.md).

## Where state lives

| Mode | Catalog | Azure CLI session |
| --- | --- | --- |
| fake | Docker volume `fake-state`, file `/var/lib/scylla-dbaas/control.db` | none |
| azure | Docker volume `azure-state`, same path | Docker volume `azure-cli`, directory `/root/.azure` |

The two volumes are separate. A cluster created in the fake UI is not the Azure cluster.

## Without Docker

The same process runs directly on the host. Use a virtualenv so the install does not touch the system Python:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
pip install -e ".[dev]"
export API_KEY=dev
export CLOUD=fake
uvicorn api.main:app --reload
```

For Azure, put the same four `AZURE_*` variables and `OPERATOR_CIDR` in the environment, then `export CLOUD=azure` before `uvicorn`. The Azure CLI on that machine runs `az login --service-principal` on the first Azure command. Leave the four variables unset only when that machine already has an Azure CLI session you intend to reuse.
