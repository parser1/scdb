# HTTP API

Base path `/api/v1`. JSON in and out. Authenticated routes require `Authorization: Bearer <API_KEY>`.

The UI is served by the same process at `/` and calls this API from the browser. The CQL client does not call this API; it uses the contact string the UI shows.

## Errors

```json
{ "error": { "code": "quorum_lost", "message": "Two data nodes are down. Replacement is disabled.", "operation_id": null } }
```

| HTTP | When |
| --- | --- |
| 400 | Body failed validation, including a bad `confirm` |
| 401 | Missing or wrong bearer token |
| 404 | Unknown id |
| 409 | Another operation is running, a second cluster was requested, or the state machine rejects the action |
| 202 | Mutation accepted and queued |
| 200 | Read, or a mutation that completed inline. Auth denial audit is inline. Everything that touches Azure is 202 |

## Clusters

### `POST /api/v1/clusters`

```json
{ "name": "demo", "location": "eastus" }
```

`name` is 1–32 characters, lowercase letters, digits, and hyphen. `location` defaults to `eastus`.

`202`

```json
{
  "cluster": {
    "id": "c_01J...",
    "name": "demo",
    "status": "provisioning",
    "location": "eastus"
  },
  "operation": {
    "id": "op_01J...",
    "type": "create_cluster",
    "status": "running",
    "step": "validate"
  }
}
```

### `GET /api/v1/clusters`

Returns an array of clusters that are not `deleted`, plus the latest operation summary for each.

### `GET /api/v1/clusters/{id}`

Cluster, nodes, latest backup, grafana URL when known, and CQL contact (`host`, `port`, `username`, `keyspace`). The password is included on this call so the demo client can be configured. It is the one intentional secret in a response, and the audit page does not echo this response.

### `POST /api/v1/clusters/{id}/deletion`

```json
{ "confirm": "demo", "delete_backups": false }
```

`202` with a `delete_cluster` operation.

## Nodes

### `POST /api/v1/clusters/{id}/nodes/{nodeId}/restart`

`202` with `restart_node`.

### `POST /api/v1/clusters/{id}/nodes/{nodeId}/replacement`

`202` with `replace_node`, or `409` with code `quorum_lost` or `operation_in_progress`.

### `GET /api/v1/clusters/{id}/nodes/{nodeId}/logs?source=`

Reads the latest lines from that VM. `source` is `cloud-init`, `scylla`, or `agent` on a data node, and `cloud-init` or `docker` on the ops node. The call runs only when the UI has that tab selected.

## Health

### `GET /api/v1/clusters/{id}/health`

```json
{
  "cluster_status": "ready",
  "automation": "enabled",
  "ops_status": "up",
  "nodes": [
    { "id": "n_01J...", "ordinal": 0, "status": "up", "private_ip": "10.0.1.10" }
  ]
}
```

`automation` is `frozen` when recovery has refused to act.

## Backups and restores

### `POST /api/v1/clusters/{id}/backups`

`202` with a `backup` operation.

### `GET /api/v1/clusters/{id}/backups`

Array of backup records, newest first.

### `POST /api/v1/clusters/{id}/restores`

```json
{ "backup_id": "b_01J...", "confirm": "demo" }
```

`202` with a `restore` operation.

## Operations and audit

### `GET /api/v1/operations/{id}`

```json
{
  "id": "op_01J...",
  "type": "create_cluster",
  "status": "running",
  "step": "create_node0",
  "error_message": null,
  "updated_at": "2026-11-05T22:00:00Z"
}
```

### `GET /api/v1/audit?cluster_id=&limit=100`

Newest first. `limit` max 500.

## UI routes

These are HTML, same origin, same API key pasted by the operator.

| Path | Page |
| --- | --- |
| `/` | Cluster list and create form |
| `/clusters/{id}` | Detail, actions, operation progress |
| `/audit` | Audit table |

The UI polls the open operation every 5 seconds and the health endpoint every 15 seconds while the tab is visible.
