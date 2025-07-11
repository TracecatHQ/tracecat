#!/usr/bin/env python3
"""Test script to demonstrate the RawTemplate annotation functionality."""

from tracecat.expressions.common import ExprContext
from tracecat.expressions.eval import eval_templated_object_selective


def test_raw_template_behavior():
    """Test that RawTemplate fields preserve their raw expression strings."""

    # Test data with template expressions
    test_data = {
        "query": "${{ ACTIONS.get_data.result.sql_query }}",
        "host": "${{ SECRETS.db.host }}",
        "database_name": "production_db",
        "parameters": ["${{ ACTIONS.get_data.result.user_id }}"],
    }

    # Mock execution context
    context = {
        ExprContext.ACTIONS: {
            "get_data": {
                "result": {
                    "sql_query": "SELECT * FROM users WHERE id = $1",
                    "user_id": "12345",
                }
            }
        },
        ExprContext.SECRETS: {"db": {"host": "localhost"}},
    }

    # Test without raw fields (regular evaluation)
    print("=== Regular evaluation (all fields evaluated) ===")
    regular_result = eval_templated_object_selective(test_data, operand=context)
    print(f"query: {regular_result['query']}")
    print(f"host: {regular_result['host']}")
    print(f"database_name: {regular_result['database_name']}")
    print(f"parameters: {regular_result['parameters']}")

    print("\n=== Selective evaluation (query field preserved as raw) ===")
    # Test with raw fields (query field preserved)
    selective_result = eval_templated_object_selective(
        test_data, operand=context, skip_keys={"query"}
    )
    print(f"query: {selective_result['query']}")  # Should be raw expression
    print(f"host: {selective_result['host']}")  # Should be evaluated
    print(f"database_name: {selective_result['database_name']}")  # Should be unchanged
    print(f"parameters: {selective_result['parameters']}")  # Should be evaluated

    # Demonstrate that the validation function can now detect expressions in raw fields
    print("\n=== Validation check ===")
    raw_query = selective_result["query"]
    if raw_query.startswith("${{") and raw_query.endswith("}}"):
        print(f"✓ Query field preserved as raw expression: {raw_query}")
        print("✓ This allows the SQL integration to validate for injection patterns")
    else:
        print("✗ Query field was not preserved as raw")


if __name__ == "__main__":
    test_raw_template_behavior()
