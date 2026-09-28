"""Reusable workflow load-test tooling.

Modules:
    models      Typed scenario, result, and artifact models.
    client      Minimal async client for the public Tracecat API.
    fixtures    Load-type workflow fixtures plus idempotent setup.
    runner      Asynchronous API load runner.
    collector   PostgreSQL activity and environment metric collector.
    matrix      CSV validation and complete cluster lifecycle orchestration.

The PostgreSQL scatter plan is the first experiment built on this harness.
"""
