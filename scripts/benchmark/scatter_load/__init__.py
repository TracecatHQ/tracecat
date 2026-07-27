"""Scaffolding for the PostgreSQL scatter load test.

Modules:
    models      Typed scenario, result, and artifact models.
    client      Minimal async client for the public Tracecat API.
    fixtures    Synthetic table and workflow fixtures plus idempotent setup.
    runner      Asynchronous API load runner.
    collector   PostgreSQL activity and environment metric collector.

See scripts/benchmark/postgres-scatter-load-test-plan.md.
"""
