# Assumptions and limitations

## Assumptions

- The operator has an Azure subscription and a service principal (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SUBSCRIPTION_ID`) with permission to create resource groups, VMs, networks, storage accounts, role assignments, and Run Command invocations. An existing Azure CLI session is used only when those identity variables are unset.
- Ubuntu 24.04 LTS Gen2 from Canonical is the image, and a current ScyllaDB open-source build publishes apt packages for noble. The URL is confirmed when `versions.env` is written, not assumed in cloud-init from memory.
- One operator, one cluster, one region (`eastus` unless overridden).
- The operator's public IP is stable for the length of the demo and is supplied as `OPERATOR_CIDR` so NSG rules can be rendered.
- Scylla Manager is allowed to own backup, restore, and repair. We do not reimplement snapshot upload.
- A 256 GiB Premium SSD is enough to store the demo keyspace. It is not sized from a capacity model.
- Clock skew between the laptop and Azure is small enough that audit timestamps can be UTC from the control plane.

## Demo limitations

- Single cluster cap. No tenants, no per-cluster RBAC, no quota beyond "one".
- Control-plane state is a file on one machine. Two API processes would corrupt it. Production uses PostgreSQL and a single active worker.
- No control-plane high availability. If the laptop sleeps mid-provision, the operation stays `running` until the process starts again and the resume logic runs.
- D-series plus Premium SSD, and possibly Scylla developer mode if setup rejects the disk. I/O numbers from this cluster are not evidence of Scylla performance.
- No autoscaling, no adding a fourth node, no second datacenter, no rack diversity. Azure fault domains are whatever the platform assigns inside one availability zone choice. We pin one zone for stable latency and accept zone failure as out of scope.
- No client-to-node TLS, no internode encryption, no customer-managed keys on the disks. Disks use Azure platform encryption.
- API auth is a shared bearer token. Audit `actor` is `operator` or `worker`, not a person.
- Grafana is protected by NSG plus its admin password, not by the API key.
- Restore targets the same cluster. There is no shadow cluster and no cutover.
- Backup retention cleanup is a worker task on a timer, not an Azure lifecycle policy. A stopped laptop will not delete an expired backup group until it runs again.
- Health probes use Run Command and are therefore minutes-scale, not seconds-scale. Prometheus is the fast signal for the human looking at Grafana. The automations are deliberately slower than the dashboard.
- CI never creates a real cluster. A live drill is a manual workflow, documented in the demo guide, and is not required for a pull request to pass.
- The CQL client is a single-row smoke test. It is not a load generator.

## What a production implementation would change

- Place the control plane on Container Apps or a small VM scale set, with PostgreSQL, a queue, and Entra ID.
- Run data nodes on local-NVMe sizes, one node per fault domain or availability zone, RF=3 across zones, after measuring cross-zone transfer cost.
- Put CQL on private endpoints. Remove the public IP on node 0.
- Enable encryption in transit, CMK if the customer requires it, and a private endpoint for the backup storage account.
- Run Manager as the supported package install on its own VM, monitoring on another, unless the Docker packaging has caught up and is a documented install path.
- Replace Run Command with a node agent that pulls desired config, so restart and target updates do not depend on a control-plane-to-guest channel that is rate limited.
- Drive repair from Manager's schedules, and page a human from Alertmanager through the company's incident tool. The quorum freeze remains.
- Restore into a new cluster, verify row counts and a checksum query, then move the client endpoint. In-place restore stays available for a single keyspace and is still confirmation-gated.
- Test backup restore on a schedule against a throwaway cluster, funded by a budget alert that pages when the subscription crosses a cap.
- Model capacity: partition size, compaction, and backup window, and reject creates that do not fit the chosen SKU.

## Cost note

Four VMs left running are the bill. The delete workflow and the single-cluster cap are the controls. A number in currency is computed at implementation time with `az` retail prices for the pinned SKU and region, and written into the demo doc, because a stale figure in this plan would be worse than none.
