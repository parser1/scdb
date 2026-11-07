# Observability and audit

Two different trails. Metrics tell you if Scylla is healthy. The audit log tells you what the control plane did. Neither replaces the other.

## Metrics

The monitoring stack on the ops VM is the pinned Scylla monitoring release, started with `start-all.sh` and an external Prometheus directory so a container restart does not wipe the demo's history.

Targets, written by the control plane into that checkout:

| File | Contents |
| --- | --- |
| `prometheus/scylla_servers.yml` | `10.0.1.10:9180`, `.11`, `.12` |
| `prometheus/node_exporter_servers.yml` | the same hosts on `9100` |

Grafana is opened from the cluster page via `http://<ops-public-ip>:3000`. The NSG allows the operator CIDR. The demo does not embed Grafana.

Alertmanager is running because it ships with the stack. v1 does not wire a pager. The control plane's own health loop is the actor that restarts and replaces. Alertmanager is there so a reviewer can see the rules upstream already ships, and so a production hook has a place to land. This is a conscious limit: we do not build a second alerting product.

## Logs

| Source | Where it goes | How the operator reads it |
| --- | --- | --- |
| API and worker | stdout, structured JSON, one object per line | The terminal running the demo, and CI log capture |
| Audit | SQLite `audit_events` | `GET /api/v1/audit` and the audit page |
| Azure control plane actions | Azure Activity Log | Linked from the cluster page as a portal URL scoped to the resource group. We do not duplicate Activity Log into SQLite |
| Scylla and system logs | Journal on the node, shipped to Loki by the monitoring stack's promtail or rsyslog path, whichever the pinned monitoring release documents | Grafana Explore |

The API does not copy journald into its database. The cluster page loads the latest lines only when a log tab is selected, through one Run Command for that tab. Refresh loads that same tab again. Loki remains the place to search across nodes once monitoring is up.

## Audit events

Written in the same transaction as the state change.

| Action | When |
| --- | --- |
| `auth.denied` | Bad or missing API key |
| `cluster.create` | Accepted, then succeeded or failed |
| `cluster.delete` | Accepted, then succeeded or failed |
| `node.restart` | Succeeded or failed |
| `node.replace` | Succeeded or failed |
| `recovery.frozen` | Two nodes unhealthy, severity `critical` |
| `backup.start`, `backup.finish` | On-demand and the detection of a scheduled run if we poll it. Scheduled runs are recorded when the health loop notices a new Manager task run id |
| `restore.start`, `restore.finish` | |
| `ops.down` | Ops VM not running |

`detail_json` may include operation id, step, Azure error code, and Manager task id. It must not include passwords, storage keys, or the API key. A unit test feeds a secret into a handler and asserts the audit row does not contain it.

## Correlation

Every worker log line includes `cluster_id` and `operation_id`. The UI operation page shows those ids so a person can match a browser action to a log line and to an Activity Log entry during the demo.
