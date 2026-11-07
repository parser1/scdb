# How this plan was produced

The homework asks for the prompts, the plan, the multi-agent instructions, and an honest account of quality and missing pieces. This file is that account for the planning pass on 2026-11-05.

## Prompt

The operator asked for a documentation folder split into architecture, design, workflow, API, and related areas, one file that references each document, and a split into tasks. The problem statement attached to that request is the homework:

Build a simplified DBaaS so a user can create and operate a 3 VM ScyllaDB cluster from a web UI and an API. Ubuntu 24.04 LTS. Scylla Manager and the Prometheus/Grafana monitor as Docker on a separate VM. Cover provisioning and lifecycle, failure recovery, backup to object storage and restore, observability, audit, an API that matches the UI, and CI. A small CQL client is a bonus. Document assumptions, decisions, limitations, and what would be different in production. Show the reasoning, the trade-offs, and the human/agent working style.

No earlier repository contents existed. The first pass was the plan. A later pass implemented the control plane, Bicep modules, cloud-init, UI, CQL client, and tests. No live Azure cluster has been created.

## What the agent did before writing

Looked up the current stable Scylla Manager and monitoring docs so the plan would follow the products instead of a memory of them:

- Manager's apt install and its requirement for its own Scylla database. The stable install page referenced the Manager 3.13 list.
- Azure Blob backup: container per region, `azure.account` on every agent's yaml, RBAC on the VM recommended, account keys discouraged, `check-location azure:<container>`.
- Monitoring stack: Docker, on a machine outside the cluster, Prometheus targets for nodes and for Manager, persistent Prometheus directory.

Those three facts are why the ops VM has a metadata Scylla container, why the managed identity sits on the data nodes rather than on Manager, and why the worker renders a targets file.

## Agents

One agent wrote this plan. The implementation split is [agent-briefs.md](../tasks/agent-briefs.md). Those briefs are instructions for a later pass, not a claim that six agents already ran. Pretending they had run would be a false demo of collaboration.

The briefs exist now because the seams are the actual risk: two agents inventing two cloud interfaces, or a UI that grows its own notion of "healthy". The shared `Cloud` port, the step names, and the rule that only the integrator edits the decision log are the controls.

## Presentation

[plan.html](../plan.html) is a single static page, generated with the docs, with no external assets. It is a briefing of the same decisions, not a second design.

## Quality of this pass

What is solid enough to implement against:

- A topology with stable IPs, a quorum guard, and backups that survive compute delete.
- Workflows with named steps, so the worker, the UI, and the tests can share a vocabulary.
- An API that returns 202 for anything that touches Azure.
- A task order with a demo checkpoint and a clear "homework is not done yet" line after it.
- Explicit fallback if Manager-in-Docker fails, so an implementer does not get stuck on the assignment's wording.

What is weak until a live subscription run:

- Package URLs, image tags, the Ubuntu URN, and the exact Scylla replace boot flag are deliberately unpinned. Filling them is T11, after a human confirms them.
- VM SKU availability in `eastus` and the real dollar cost are not checked.
- Whether the 256 GiB Premium disk passes `scylla_setup` without developer mode is an assumption. The plan allows developer mode and requires the cluster to confess it via `io_profile`.
- Run Command latency may make the health loop too slow to be a satisfying demo. If the first live restart takes an embarrassing amount of time, the fix is to document the observed minutes and, if needed, add one direct CQL probe from the control plane to the private IP. That probe only works if the laptop can route to the VNet, which it cannot without a VPN or by jumping through the ops VM. The honest fix is to keep Run Command and show Grafana as the fast signal.
- Manager's Docker image may refuse the metadata-container layout. The fallback is already accepted in D5. Using the fallback is a successful outcome, not a failed assignment, as long as the decision log records it.

## Missing on purpose

- Multi-tenant control plane, Entra ID, TLS, zone-spanning RF, local NVMe, and restore-to-a-new-cluster.
- A pager destination for Alertmanager.
- Automated restore drills inside CI.
- HA for the API process.

These are written into [assumptions-and-limitations.md](../design/assumptions-and-limitations.md) so they are not rediscovered as surprises in review.

## What would change this plan

A live T25 run that contradicts a step name, a port, or the Manager install path. The integrator updates the workflow doc and the decision log in the same change. The HTML briefing is regenerated or hand-edited so it does not keep a stale picture.
