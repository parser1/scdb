# ScyllaDB on Azure — simplified DBaaS

A control plane that provisions and operates one 3-node ScyllaDB cluster on Azure. Scylla Manager and Prometheus/Grafana run as Docker on a separate VM.

Start here: [docs/getting-started.md](docs/getting-started.md).

```bash
docker compose up --build
```

That serves the UI at http://127.0.0.1:8000 with API key `dev` and does not call Azure. The Azure image is the same UI pointed at your subscription. Copy the example file and fill it in first ([Getting started](docs/getting-started.md)):

```bash
cp .env.example .env
```

`.env` is not committed. The fake image only needs `API_KEY`. The Azure image needs every value below.

| Variable | Where to get it |
| --- | --- |
| `API_KEY` | A password you choose for this UI and API. The example uses `dev`. Type the same value into the header on http://127.0.0.1:8000 and press Save. |
| `OPERATOR_CIDR` | Your current public IP plus `/32`. From the repository root, `curl -fsS https://api.ipify.org` prints the address. If it prints `203.0.113.10`, the value is `203.0.113.10/32`. The network rules allow that address to reach CQL and Grafana. |
| `AZURE_LOCATION` | The Azure region for the virtual machines, such as `eastus`. In the portal, the region list is on any resource's Basics page. Leave `eastus` unless you want another region. |
| `AZURE_ZONE` | The availability zone number inside that region: `1`, `2`, or `3`. Leave `1` unless you need a different zone. The region must offer that zone. |
| `AZURE_TENANT_ID` | Microsoft Entra ID → App registrations → your registration → Overview → Directory (tenant) ID. |
| `AZURE_CLIENT_ID` | The same Overview page → Application (client) ID. |
| `AZURE_CLIENT_SECRET` | That app → Certificates & secrets → New client secret. Copy the secret **Value**. The portal shows it only once. |
| `AZURE_SUBSCRIPTION_ID` | Subscriptions → the subscription where that app is **Owner**, or **Contributor** plus **User Access Administrator** → Overview → Subscription ID. |

Create the app registration before copying those four IDs: Entra ID → App registrations → New registration, then the secret and the role assignment above. Then start the Azure image:

```bash
docker compose --profile azure up --build azure
```

The design lives in [docs/README.md](docs/README.md). The briefing is [docs/plan.html](docs/plan.html).
