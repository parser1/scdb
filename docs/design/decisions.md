# Decision log

Each decision is accepted for this homework. Reopening one means editing this file and the documents that implement it.

## D1. One control plane, one cluster at a time

The API and worker run as one Python process on the operator machine, with SQLite beside it. A second cluster is rejected while the first exists.

We set aside a control plane on Azure Container Apps and a PostgreSQL catalog. Those are the production homes for this service. They add a deploy path before the first node exists, which is the wrong order for a demo. The reconcile task in the backlog exists so a lost SQLite file is recoverable from Azure tags.

## D2. Bicep modules applied by the worker

Static resources (network, NSG, storage, identity, ops VM, data VM) are Bicep modules. The worker deploys them with the Azure SDK or `az deployment group create`. Node replace deploys the data-VM module again onto the existing NIC.

We set aside a pure imperative SDK with no templates, because CI could not review the layout. We set aside Terraform with a remote state backend, because it is a second control plane to host. Bicep keeps the reviewable artifact in this repo and the lifecycle logic in the worker.

## D3. Fixed 3-node topology

Every cluster is three data nodes, RF=3, one datacenter, one rack. There is no scale-out, no multi-rack awareness, and no add-datacenter flow.

The homework asks for a 3 VM cluster. Elastic topology would dominate the project and is the first thing a production version would design properly, including token ownership and tablet or vnode migration.

## D4. NICs and identities outlive the VM

Each data node has a NIC and a user-assigned managed identity created before the VM. Replacement attaches both to the new VM and allocates a new data disk. The private IP stays.

We set aside "new VM, new IP, edit seeds." Stable addresses keep Manager, Prometheus, and the CQL client valid across recovery.

## D5. Manager and monitoring share the ops VM, in Docker

The ops VM runs Manager's metadata Scylla, Manager, and the monitoring stack. Data nodes run only Scylla and the Manager agent.

Monitoring-in-Docker matches upstream. Manager-in-Docker matches the assignment text and is less documented than the apt install. If the image cannot complete `cluster add` and a backup, the ops cloud-init switches to `scylla-manager-server` from the 3.13 apt repository and a local Scylla installed by `scyllamgr_setup`. The user-facing behavior stays the same. The cluster record stores `manager_install=docker|package`.

## D6. Backups live in a second resource group

Compute delete is immediate. The backup resource group remains until retention expires (default 7 days) unless the delete call sets `delete_backups=true`.

We set aside storing blobs inside the compute group. That makes the first successful delete the moment the backups disappear.

## D7. Recovery automates one node, and stops at quorum loss

Automation may start a stopped VM, restart a dead `scylla-server`, or replace one node when the other two are up. If two data nodes are unhealthy, automation freezes, cluster status becomes `degraded`, and an audit event of severity `critical` is written.

Replacing into a quorum-less cluster can lose the remaining replica's updates. A human is the right actor there.

## D8. Async operations

Create, delete, restart, replace, backup, and restore return `202` with an operation id. The UI polls `GET /api/v1/operations/{id}`.

Long Azure and streaming steps do not fit an HTTP request. The operation row is also the progress surface for the demo.

## D9. API key now, Entra ID later

Demo auth is a bearer token compared to `API_KEY` in the environment. The UI is a same-origin form that sends the key the operator pasted into the session.

Entra ID, per-user audit identity, and Grafana SSO are the production replacement. Building them first would not teach anything about the database.

## D10. Server-rendered UI in the API process

Pages are templates served by the API. There is no second frontend deploy.

A separate SPA is justified once a designer is iterating on the UI independently of the worker. Here the UI is a thin view of operations, health, and backups.

## D11. Run Command instead of SSH keys

Remote steps (wait for `nodetool status`, write Prometheus targets, restart a unit, create a CQL role) go through Azure VM Run Command.

We set aside an SSH key in the control plane. Run Command is slower and rate-limited, and it shows up in the Azure Activity Log, which we want. The limitation is accepted for four VMs.

## D12. Demo VM size, production size documented

Demo nodes are D-series with Premium SSD. The production note in the topology doc specifies local-NVMe Lsv3 and no developer mode. We do not pretend the demo size is a benchmark platform.

## D14. Azure CLI and a built-in backup role

The worker deploys Bicep and runs VM commands through the Azure CLI. When `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID` are set, the first command signs in with that service principal. The secret stays in the environment and the CLI config volume, not in git. When those identity variables are unset, the worker uses the current Azure CLI session. A custom role definition is not created during provisioning. Data VMs are assigned the built-in Storage Blob Data Contributor role on the backup account, which covers the blob data actions Manager's agent needs and does not require permission to invent roles.

`replace_node_first_boot` is the only replace flag. Current Scylla docs mark `replace_address_first_boot` as deprecated and unsupported.

## D13. Language

Python for the API, worker, cloud-init rendering, tests, and CQL client. One test toolchain, a mature Azure SDK, and the CQL driver we need anyway.

Go would be a strong production choice for a long-running worker. It is not worth a second language on a homework deadline.
