# CI and demo

## CI

GitHub Actions on pull request. No Azure credentials in the repository. No `az login`.

| Job | Does |
| --- | --- |
| `test` | `pytest` for store invariants, workflows on FakeCloud, API handlers, audit redaction, OpenAPI snapshot |
| `bicep` | `az bicep build` on every module. Uses the Bicep CLI, not a subscription |
| `templates` | Render cloud-init for node 0, node replace, and the ops compose. Parse YAML. `docker compose config` when Docker is installed |
| `audit-drift` | Compare action names in `docs/workflow/observability-and-audit.md` to the constants in code |

A manual workflow `live-demo.yml` is `workflow_dispatch` only. It is documented as a placeholder that prints the runbook steps. It does not become an unattended cluster factory. Unattended creates are how demo subscriptions get forgotten.

Pull request CI is the homework's CI requirement. The live subscription run is a human demo, recorded in `demo-log.md`.

## Local demo order

Run this after T25 has succeeded once, so the pins are real. Full narrative is the runbook written in T24. The sequence the UI demo must cover:

1. Put `API_KEY`, `OPERATOR_CIDR`, and the four `AZURE_*` service-principal values in `.env`, or export them. See [Getting started](../getting-started.md).
2. Start the API on the laptop.
3. Open `/`, paste the key, create cluster `demo` in `eastus`.
4. Watch the operation step list until `ready`. Expect tens of minutes, dominated by package install and Scylla first boot.
5. Open the Grafana link. Confirm three Scylla targets are up.
6. Run `clients/cql_demo` with the contact shown on the cluster page. Insert and read.
7. Start a backup. Wait until the backup row is `succeeded`. Confirm a prefix in the container.
8. Insert another row. Restore. Read again and show the restored set.
9. Deallocate one data VM in the portal, or stop it. Watch restart or replace. For the replace proof, delete the VM resource in the portal and leave the NIC. Confirm the same private IP comes back UN.
10. Stop two data VMs. Confirm status `degraded`, automation frozen, audit `recovery.frozen`.
11. Start those VMs again so delete is clean, then delete the cluster with backups retained. Confirm the compute group is gone and the backup group remains. Delete backups from the UI path or the runbook's `az group delete` when finished.

## Browser check

T15 and T28 include a real browser pass of the three pages: create, the disabled-button states, audit rows appearing after an action, and delete confirmation rejecting a wrong name. A screenshot of the detail page is not the check. The operation step has to change on the page while a FakeCloud or live operation is running.
