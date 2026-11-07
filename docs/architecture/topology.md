# Azure topology

One cluster uses two resource groups, one virtual network, four virtual machines, and one storage account.

## Resource groups

| Group | Name | Lifetime |
| --- | --- | --- |
| Compute | `rg-scylla-<clusterId>` | Deleted when the cluster is deleted |
| Backup | `rg-scylla-<clusterId>-bak` | Kept for the retention window after cluster delete, unless the operator asks to delete backups immediately |

Every resource is tagged `app=scylla-dbaas`, `cluster-id=<id>`, and `role=data|ops|network|backup`. Tags are how a later reconcile pass recognizes resources the local database has forgotten.

## Network

| Object | Value |
| --- | --- |
| VNet | `10.0.0.0/16` |
| Subnet | `10.0.1.0/24` |
| Ops VM | `10.0.1.4` |
| Data node 0 | `10.0.1.10` |
| Data node 1 | `10.0.1.11` |
| Data node 2 | `10.0.1.12` |

Addresses are private and reserved on NICs that outlive the VM. Node replacement creates a new VM on the existing NIC, so seeds, Manager, and Prometheus keep the same targets.

Nodes 1 and 2 use seeds `10.0.1.10` and `10.0.1.11`. Node 0's first boot lists only itself. Listing `.11` before that VM exists leaves Scylla in `STARTING`. Node 0 is waited on before nodes 1 and 2. A replacement omits its own address from that list. Node 0 replaces through `.11`, node 1 replaces through `.10`, and node 2 still uses both. Scylla refuses `replace_node_first_boot` on a seed, so a replacement that lists itself never becomes UN.

Public IPs:

| Use | Attachment | Who can connect |
| --- | --- | --- |
| Grafana and SSH-less operator access to the ops VM | Ops NIC | Operator public IP only |
| CQL for the bonus client | Data node 0 NIC | Operator public IP only, port 9042 |
| Everything else | none | Private subnet only |

The control plane itself is not one of these VMs. For the demo it runs on the operator machine and calls Azure. Moving it to Azure Container Apps is a production step, not a demo step.

## NSG

Default is deny from the internet. The subnet allows the cluster to talk to itself.

| Port | Purpose | Source |
| --- | --- | --- |
| 7000, 7001 | Inter-node gossip and streaming | Subnet |
| 9042 | CQL | Subnet, plus operator IP to node 0 |
| 9180 | Scylla Prometheus metrics | Ops VM |
| 9100 | node_exporter | Ops VM |
| 10001 | Scylla Manager agent | Ops VM |
| 22 | Admin, via Azure Bastion or operator IP | Operator IP |
| 3000 | Grafana | Operator IP to ops VM |
| 9090 | Prometheus | Operator IP to ops VM |
| 5080 | Manager HTTP API | Subnet and operator IP |

JMX 7199 stays on the loopback of each data node. The control plane uses Azure Run Command and Manager, not JMX.

## Machines

| Role | Count | Demo size | Disks |
| --- | --- | --- | --- |
| Data | 3 | `Standard_D4ds_v5` (4 vCPU, 16 GiB) | 32 GiB OS Premium, 256 GiB Premium SSD data disk at `/var/lib/scylla` |
| Ops | 1 | `Standard_D4s_v5` (4 vCPU, 16 GiB) | 64 GiB OS Premium. Prometheus TSDB under `/var/lib/scylla-monitoring` |

Image: Ubuntu 24.04 LTS Gen2, Canonical. The exact URN is selected with `az vm image list` at implementation time and stored in `versions.env`. `latest` is acceptable for a first demo and is then pinned.

These sizes are a cost choice for a homework cluster. Scylla's production guidance on Azure is a storage-optimized size with local NVMe (Lsv3 family) and a disk layout `scylla_setup` accepts without developer mode. If the 256 GiB Premium disk fails Scylla's setup checks, cloud-init may set developer mode so the demo can boot, and the cluster record will show `io_profile=demo`. A cluster in that mode is not a performance exhibit.

## Region

Default `eastus`, overridable in the create request. The backup storage account is created in the same region. One region only. There is no cross-region replica in this design.

## Quota guard

The worker refuses to provision when the subscription cannot supply 4 VMs of the chosen size, 4 public-IP-or-less NIC allocations, and a storage account. The API also refuses a second cluster while one exists. That cap is deliberate: a forgotten demo cluster of four VMs is the main cost risk.
