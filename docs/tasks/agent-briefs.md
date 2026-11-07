# Agent briefs

Use these when the build is split across agents. One agent owns a brief. The integrator owns the seams and is the only agent who edits the decision log.

Shared rules for every agent:

- Read [docs/README.md](../README.md) and the decision log before writing code.
- Stay inside the task ids in your brief. If you need a decision changed, stop and write the proposal in your summary. Do not silently diverge.
- Secrets never go in git, logs, fixtures, or Bicep parameter examples.
- Tests use `FakeCloud`. Do not point unit tests at a real subscription.
- Match the repository layout reserved in the docs map.

## Agent A — control plane

Tasks: T01, T02, T03, T04, T05, T13, T14, T20, T23.

Build the Python process, store, worker, API, and FakeCloud workflows. `AzureCloud` may remain unimplemented in your branch if Agent C has not landed the port; program against the `Cloud` protocol only.

Done when: `pytest` is green on FakeCloud for create, delete, auth, audit redaction, and the OpenAPI snapshot.

## Agent B — infrastructure

Tasks: T06, T07, T08, T09, T10, T11.

Bicep, cloud-init, compose, `versions.env`. No FastAPI handlers.

Done when: `az bicep build` and the cloud-init render tests pass, and `versions.env` names the Manager install path you actually booted once.

You need a subscription for the single ops-compose boot in T11. If you do not have one, leave the pins blank and say so. Do not invent version numbers.

## Agent C — Azure adapter

Tasks: T12, T21.

Implement `AzureCloud` and the monitoring target copy. Depends on Agent A's protocol and Agent B's modules.

Done when: methods match the protocol, Run Command payloads are redacted in logs, and the targets golden file test passes.

## Agent D — UI

Tasks: T15.

Consume the API only. Do not import the worker.

Done when: the three pages render against a running API on FakeCloud, dangerous buttons are gated, and the restore helper text states that restore overwrites keyspace `demo` in place.

## Agent E — recovery and backup behavior

Tasks: T17, T18, T19.

Depends on Agent A's worker and the Fake Manager/Cloud doubles. If a double does not exist, add it under `tests/fakes` without changing production step names.

Done when: the classification table, quorum guard, IP-stable replace, and restore confirmation are covered by tests.

## Agent F — delivery

Tasks: T16, T22, T24.

CQL client, GitHub Actions, and the runbook skeleton. Leave T25–T28 to the human operator; you may script the checklist those tasks fill in.

Done when: CI matches [ci-and-demo.md](../delivery/ci-and-demo.md) and the client has a `--help` that matches the runbook command.

## Integrator

Tasks: T25, T26, T27, T28, and any seam that two agents interpreted differently. T29 if time remains.

- Run the live demo.
- When Azure or Scylla disagrees with a doc, change the doc and the code together and add a dated line to the decision log.
- Fill `docs/delivery/demo-log.md` with what was shown and what failed.
- Do not mark the homework done on the demo checkpoint alone. T26 and T27 are part of done.
