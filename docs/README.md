# Documentation map

Plan for a demoable ScyllaDB DBaaS on Azure. Written 2026-11-05, before implementation. Each document below is the source of truth for its slice. If a later change contradicts one of them, update the decision log and this map in the same change.

| Area | Document | What it decides |
| --- | --- | --- |
| Architecture | [architecture/overview.md](architecture/overview.md) | System context, trust boundaries, what we build and what we reuse |
| Architecture | [architecture/topology.md](architecture/topology.md) | Azure layout, addresses, ports, VM sizes, disks |
| Architecture | [architecture/components.md](architecture/components.md) | Process responsibilities and who calls whom |
| Design | [design/decisions.md](design/decisions.md) | Decision log and the alternatives we set aside |
| Design | [design/data-model.md](design/data-model.md) | Control-plane records and cluster state machine |
| Design | [design/security.md](design/security.md) | Identity, network exposure, secrets, confirmation gates |
| Design | [design/assumptions-and-limitations.md](design/assumptions-and-limitations.md) | Assumptions, demo limits, and what a production version would change |
| Workflow | [workflow/provisioning.md](workflow/provisioning.md) | Create path, from API call to a queryable cluster |
| Workflow | [workflow/lifecycle.md](workflow/lifecycle.md) | Day-2 operations and delete, including backup retention |
| Workflow | [workflow/failure-recovery.md](workflow/failure-recovery.md) | Detection, restart, node replace, quorum guard |
| Workflow | [workflow/backup-and-restore.md](workflow/backup-and-restore.md) | Manager backups to Azure Blob and restore |
| Workflow | [workflow/observability-and-audit.md](workflow/observability-and-audit.md) | Metrics, logs, and the audit trail |
| API | [api/overview.md](api/overview.md) | HTTP API the UI and the CQL demo share |
| Tasks | [tasks/backlog.md](tasks/backlog.md) | Ordered build tasks with acceptance criteria |
| Tasks | [tasks/agent-briefs.md](tasks/agent-briefs.md) | How to split the build across agents |
| Delivery | [delivery/ci-and-demo.md](delivery/ci-and-demo.md) | CI gates and the operator demo script |
| Delivery | [getting-started.md](getting-started.md) | Run the fake or Azure control plane with Docker |
| Delivery | [delivery/runbook.md](delivery/runbook.md) | Live create, failure commands, and cost |
| Delivery | [delivery/collaboration.md](delivery/collaboration.md) | Prompt, research, quality bar, known gaps |
| Briefing | [plan.html](plan.html) | Single-page presentation of this plan |

## Target repository layout

Implementation tasks create this tree. Paths are reserved now so agents do not invent competing layouts.

```
api/                 HTTP service, auth, handlers
worker/              operation runner, health loop, Azure adapter
infra/bicep/         resource group, network, identity, VM modules
infra/cloud-init/    data-node and ops-node templates
ops/compose/         Manager metadata Scylla, Manager, monitoring stack
ui/                  templates served by the API process
clients/cql_demo/    bonus CQL insert/read client
tests/               unit tests with a fake cloud adapter
.github/workflows/   CI
versions.env         pinned image and package versions
```

## Demo checkpoint vs full rubric

The first demo is a cluster that comes up from the UI and accepts CQL. The homework is not done at that checkpoint. Recovery, backup, restore, audit, and CI are required and are sequenced after the checkpoint in the backlog.
