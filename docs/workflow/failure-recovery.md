# Failure recovery

The health loop runs every 60 seconds while a cluster is `ready` or `degraded`. It is slow because each deep check uses Run Command. Azure power state is checked on every tick through the API, which is cheap. A Run Command nodetool check runs when power state is not `running`, or every fifth tick.

## Classification

For each data node:

| Observation | Node status | Next action |
| --- | --- | --- |
| VM running, nodetool UN | `up` | None |
| VM stopped or deallocated | `down` | Queue `restart_node` with step `start_vm` |
| VM running, CQL closed or nodetool not UN | `down` | Queue `restart_node` with step `systemctl_restart` |
| VM failed, missing, or `systemctl_restart` already failed once for this incident | `down` | Queue `replace_node` if the quorum guard passes |
| Two data nodes not `up` | cluster `degraded` | Queue nothing. Audit `recovery.frozen` severity `critical` |

Ops VM failure is detected and audited. It is not auto-replaced in v1. Manager and Grafana being down does not mean user data is down, and recreating the ops VM requires re-registering Manager. The UI shows `ops_status=down` and the runbook says to run delete or to file a follow-up. Auto-replace of the ops VM is a production item.

An incident id is the node id plus the timestamp of the transition to `down`. Retries are counted on that incident so a restart loop cannot run all night. After one failed restart, the next tick proposes replace.

## Restart

| Step | Action |
| --- | --- |
| `start_vm` | If power state is not running, start the VM and wait until the agent is ready |
| `systemctl_restart` | Run Command `systemctl restart scylla-server`, then poll nodetool until UN or 5 minutes |
| `verify` | Node status `up`, audit `node.restart` |

## Replace

Preconditions, all required:

- The other two data nodes are `up`.
- This node has a stored `host_id`.
- No other operation is running.
- The cluster is not `restoring`.

| Step | Action |
| --- | --- |
| `detach` | If the VM resource still exists, delete the VM and its data disk. Keep the NIC, public IP if any, and user-assigned identity |
| `deploy` | Deploy the data-VM module on that NIC with a new data disk. cloud-init includes the replace boot flag for the stored `host_id`. The seed list omits this node's own IP, because Scylla will not replace a seed and the node would never reach UN |
| `wait_un` | Poll until the new node is UN and the other two stayed UN. Timeout 30 minutes, streaming a 256 GiB disk of demo data will be far under that |
| `refresh_monitoring` | Targets did not change if the IP was kept. Still reload Prometheus so a stale "down" series is not the thing the operator stares at |
| `repair` | Start a Manager repair. The operation stays `running` until Manager reports the repair finished or failed |
| `verify` | Node `up`, cluster `ready`, audit `node.replace` |

`versions.env` sets `SCYLLA_REPLACE_FLAG=replace_node_first_boot`. Current Scylla docs define that flag as the host id of the dead node and mark `replace_address_first_boot` as unsupported. The private IP stays stable because the NIC is reused.

## What we do not automate

- Loss of two nodes. Status `degraded`, automation frozen, the UI says so in one sentence.
- Disk full, bad queries, or a user dropping the `demo` keyspace. Those are visible in Grafana and audit only if the user did it through the API. A CQL drop is in the Scylla log, not in our audit log. That gap is accepted and stated on the audit page.
- Region or zone loss.
- Split brain. With RF=3 and a refuse-to-replace-below-quorum rule, the demo's failure story is the single node.
