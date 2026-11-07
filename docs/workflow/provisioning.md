# Provisioning

`POST /api/v1/clusters` inserts the cluster as `pending`, inserts a `create_cluster` operation as `queued`, and returns `202`. The worker then runs the steps below. Each step is idempotent: if the named Azure resource exists and the tags match, the step continues.

## Steps

| Step | Action | Succeeds when |
| --- | --- | --- |
| `validate` | Name, region, operator CIDR, single-cluster cap, deployer credentials | Inputs accepted |
| `create_groups` | Compute RG and backup RG | Both exist |
| `create_network` | VNet, subnet, NSG with the rules in the topology doc | Subnet id returned |
| `create_backup_storage` | Storage account and container `scylla-backup` in the backup RG. Account name derived from the cluster id, lowercased, legal for storage | Container exists |
| `create_identity` | User-assigned identity and the backup role assignment | Principal id returned. Role assignment can take a minute; the step polls |
| `create_nics` | Four NICs with the static private IPs. Public IP on ops and on data node 0, scoped by NSG | NIC ids stored on node rows |
| `create_ops_vm` | Ops VM, cloud-init installs Docker and writes the compose file. Does not start Manager until targets exist; it may start the metadata Scylla container | VM running, cloud-init done |
| `create_node0` | Data VM ordinal 0. cloud-init installs Scylla, formats the data disk, starts the node as the first seed | Run Command reports CQL up |
| `create_node1_node2` | Data VMs 1 and 2 in parallel. Seeds point at `.10` and `.11` | Both report UN and node 0 still UN |
| `capture_host_ids` | Read `host_id` from each node and store it | Three host ids saved |
| `configure_auth` | On node 0's maintenance socket, create `scylla_manager` and `demo_app`, create keyspace `demo` with RF=3 | The command prints `DBAAS_OK` |
| `configure_agents` | Set `azure.account`, restart agent, `check-location azure:scylla-backup` | Command exits 0 on all three |
| `register_manager` | Start Manager container if needed. Register the cluster with the three private IPs and the manager role | `sctool status` shows three UN |
| `configure_monitoring` | Write the Scylla and node_exporter target files, then run `start-all.sh` from the pinned monitoring release. Prometheus data stays on the host | Grafana answers on port 3000 from the ops VM |
| `schedule_tasks` | Manager repair schedule, weekly. Backup schedule, daily, retention 7, location `azure:scylla-backup` | Task ids stored |
| `ready` | Cluster status `ready`, audit `cluster.create` succeeded | API `GET` shows `ready` |

Any failed step sets the operation to `failed`, the cluster to `error`, and writes the audit event. Resources already created are left in place so the operator can read cloud-init logs and then delete.

## Bootstrap ordering

Node 0 must accept CQL before nodes 1 and 2 boot. cloud-init on nodes 1 and 2 may start Scylla immediately and retry joining; the worker still waits until status is UN rather than trusting cloud-init's exit code alone. A node that stays DN for 15 minutes fails the step.

## cloud-init contents, data node

The template is rendered per node. Parameters: cluster name, ordinal, private IP, seed list, backup account name, package versions.

1. Wait for apt locks, install the Scylla repo, install the pinned server, manager-agent, and `scylla-node-exporter` packages.
2. Partition and mount the data disk at `/var/lib/scylla`.
3. Write `scylla.yaml` and rackdc properties. Snitch `GossipingPropertyFileSnitch`. `rpc_address` and `listen_address` are the private IP. `api_address` is `127.0.0.1`, which is where `nodetool` connects. `broadcast_rpc_address` is the private IP. The public IP is only an NSG path to node 0, not a gossip address. The data disk is the Azure LUN 0 symlink, including the NVMe path on v6 sizes, or the unmounted disk whose size matches the data disk.
4. Run noninteractive setup. If setup refuses the disk, write `developer_mode: true` and record that fact for the worker to store as `io_profile=demo`.
5. Enable and start `scylla-server`, `scylla-manager-agent`, and `scylla-node-exporter`. Node exporter listens on port 9100.

## cloud-init contents, ops node

1. Install Docker Engine and the compose plugin from Ubuntu's Docker packages.
2. Write `/opt/scylla-ops/compose.yml` for Manager and its metadata database.
3. Clone the pinned Scylla monitoring release to `/opt/scylla-monitoring`. A failed clone fails bootstrap.
4. Start the metadata Scylla container.
5. Leave Manager and the monitoring stack stopped until the worker writes target files and starts them.

## What "ready" means

All three data nodes are UN, Manager has the cluster, Prometheus targets include three nodes, keyspace `demo` exists, and both schedules exist. The UI is allowed to show the CQL connection string only in this state.
