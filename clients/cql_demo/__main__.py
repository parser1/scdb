"""CLI: python -m clients.cql_demo --host HOST --username demo_app --password SECRET"""

from __future__ import annotations

import argparse
import sys

from clients.cql_demo.client import read_only, write_and_read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Insert and read one row on a ScyllaDB cluster")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=9042)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--body", default="hello from scylla-dbaas")
    parser.add_argument("--read", action="store_true", help="Read demo.events demo-1 without writing")
    args = parser.parse_args(argv)
    try:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
    except ImportError:
        print("Install the driver: pip install 'cassandra-driver>=3.29'", file=sys.stderr)
        return 2
    cluster = Cluster(
        [args.host],
        port=args.port,
        auth_provider=PlainTextAuthProvider(args.username, args.password),
    )
    session = cluster.connect()
    try:
        row = read_only(session) if args.read else write_and_read(session, args.body)
    finally:
        cluster.shutdown()
    if row is None:
        print("no row")
        return 1
    print(f"{row.id}\t{row.body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
