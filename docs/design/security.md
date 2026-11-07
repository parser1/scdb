# Security

Demo-grade on purpose, with the production replacement named for each item.

## Authentication

The API expects `Authorization: Bearer <API_KEY>`. Missing or wrong keys return 401 and an audit event `auth.denied`. The key lives in the environment, never in git.

The UI asks for the key once and keeps it in `sessionStorage`. It is not written to the control-plane database.

Production replacement: Entra ID on the API, a group for operators, and the `actor` column set to the user object id.

## Azure identities

| Identity | Held by | Rights |
| --- | --- | --- |
| Deployer | Service principal from `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, and `AZURE_SUBSCRIPTION_ID`. An existing `az login` is used only when those three identity variables are unset | Owner, or Contributor plus User Access Administrator, so provisioning can write the backup role assignment |
| Cluster backup identity | User-assigned, attached to the three data VMs | Built-in Storage Blob Data Contributor, scoped to the backup storage account |

The Manager container does not receive Azure credentials. Agents on the data VMs upload snapshots using the VM identity and the instance metadata service.

Storage account keys are not generated into config. The agent yaml sets `azure.account` and leaves `azure.key` commented.

## Secrets generated per cluster

| Secret | Where it is used | Where it is stored |
| --- | --- | --- |
| `demo_app` password | CQL client and the UI detail page | `clusters.cql_secret` |
| `scylla_manager` password | Manager server cluster registration | `clusters.manager_secret` |
| Grafana admin password | Ops VM env file | Ops VM only, mode 0600, also copied into `clusters` if we need to show it. Same redaction rules |

Logs, operation `request_json`, audit `detail_json`, and Bicep parameter files passed on the command line use the literal `<redacted>` for these values. Tests fail if a fixture password appears in a log capture.

## Network

See [topology](../architecture/topology.md). CQL, Grafana, Prometheus, and Manager are not open to `0.0.0.0/0`. Metrics ports accept the ops VM only.

## Dangerous actions

| Action | Gate |
| --- | --- |
| Delete cluster | JSON `confirm` equals the cluster name |
| Restore | JSON `confirm` equals the cluster name, and status is `ready` |
| Replace node | Other two data nodes are `up`. No extra typed confirm, because the quorum guard is the safety check. The UI still requires a button click, not a hover |
| Restart node | No name confirm. Restart is the low-impact first step |

## Data plane auth

Scylla runs with `authenticator: PasswordAuthenticator` and `authorizer: CassandraAuthorizer`. Scylla 2026 does not create a `cassandra`/`cassandra` superuser. Provisioning creates `scylla_manager` through the node 0 maintenance socket, then `demo_app`. `demo_app` has access to keyspace `demo` only. `scylla_manager` has the rights Manager's docs require for schema backup.

Client-to-node TLS and inter-node encryption are out of scope for the demo and listed under limitations. A production cluster on a shared subnet would turn both on and would not publish CQL on a public IP at all. The demo publishes node 0's CQL to the operator IP so the bonus client can run from the laptop without a tunnel. That rule is removed in a production design in favor of a private endpoint or Bastion tunnel.
