"""CQL client logic without a live cluster."""

from types import SimpleNamespace

from clients.cql_demo.client import ROW_ID, write_and_read


class FakeResult:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class FakeSession:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if query.strip().upper().startswith("SELECT"):
            return FakeResult(SimpleNamespace(id=ROW_ID, body=params[0] if False else "hello from scylla-dbaas"))
        return FakeResult(None)


def test_write_and_read_round_trip():
    session = FakeSession()
    row = write_and_read(session, "hello from scylla-dbaas")
    assert row.id == ROW_ID
    assert row.body == "hello from scylla-dbaas"
    assert any(params == (ROW_ID, "hello from scylla-dbaas") for _query, params in session.queries)
