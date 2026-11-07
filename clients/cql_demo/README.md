# CQL demo client

Reads the contact line from the cluster page (`demo_app`, node 0's public address) and checks one row. Run these from the repository root.

```bash
python -m clients.cql_demo --host 203.0.113.10 --username demo_app --password 'THE_PASSWORD'
python -m clients.cql_demo --host 203.0.113.10 --username demo_app --password 'THE_PASSWORD' --read
```

Install the driver first:

```bash
pip install 'cassandra-driver>=3.29'
```
