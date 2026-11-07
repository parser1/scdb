"""Small CQL smoke client for a cluster this control plane created."""

from __future__ import annotations

KEYSPACE_CQL = (
    "CREATE KEYSPACE IF NOT EXISTS demo WITH replication = "
    "{'class': 'NetworkTopologyStrategy', 'datacenter1': 3}"
)
TABLE_CQL = "CREATE TABLE IF NOT EXISTS demo.events (id text PRIMARY KEY, body text)"
INSERT_CQL = "INSERT INTO demo.events (id, body) VALUES (%s, %s)"
SELECT_CQL = "SELECT id, body FROM demo.events WHERE id = %s"
ROW_ID = "demo-1"


def write_and_read(session, body: str = "hello from scylla-dbaas"):
    try:
        session.execute(KEYSPACE_CQL)
    except Exception as exc:
        # Provisioning creates keyspace demo and grants it to demo_app.
        # That role cannot CREATE KEYSPACE, so an existing keyspace is enough.
        if exc.__class__.__name__ != "Unauthorized":
            raise
    session.execute(TABLE_CQL)
    session.execute(INSERT_CQL, (ROW_ID, body))
    result = session.execute(SELECT_CQL, (ROW_ID,))
    row = result.one()
    if row is None:
        raise RuntimeError("inserted row was not readable")
    return row


def read_only(session):
    result = session.execute(SELECT_CQL, (ROW_ID,))
    return result.one()
