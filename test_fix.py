#\!/usr/bin/env python3
"""Test script to validate the secret extraction fix."""

import re
import sys
import os

# Add the workspace to the path
sys.path.insert(0, '/workspace')

# Import the patterns module
from tracecat.expressions import patterns

def test_extract_templated_secrets(templated_obj):
    """Test implementation of extract_templated_secrets with the fix."""
    secrets = set()
    
    # Pattern to match quoted strings (both single and double quotes)
    quoted_string_pattern = re.compile(r"""'[^']*' < /dev/null | "[^"]*\"""")
    
    inner_secret_pattern = re.compile(
        r"SECRETS\.(?P<secret>[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)"
    )

    def operator(line):
        """Collect secrets from template expressions in the string."""
        for tmpl in re.finditer(patterns.TEMPLATE_STRING, line):
            expr = tmpl.group("expr")
            
            # Find all quoted string ranges in the expression
            quoted_ranges = []
            for quoted_match in quoted_string_pattern.finditer(expr):
                quoted_ranges.append((quoted_match.start(), quoted_match.end()))
            
            # Find all secret matches and filter out those inside quoted strings
            for match in re.finditer(inner_secret_pattern, expr):
                match_start, match_end = match.span()
                
                # Check if this match is inside any quoted string
                inside_quotes = any(
                    start <= match_start < end and start < match_end <= end
                    for start, end in quoted_ranges
                )
                
                if not inside_quotes:
                    secrets.add(match.group("secret"))

    def process_obj(obj):
        """Process object recursively."""
        if isinstance(obj, str):
            operator(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str):
                    operator(k)
                process_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                process_obj(item)

    process_obj(templated_obj)
    return list(secrets)


def run_tests():
    """Run test cases to validate the fix."""
    print("Testing extract_templated_secrets fix...")
    
    # Test case 1: should ignore secrets in string literals
    print("\n=== Test 1: String literal (should be ignored) ===")
    test_str1 = "SECRETS.a.K1 not inside template; and ${{ 'SECRETS.a.K1' }} as string"
    print(f"Testing: {test_str1}")
    result1 = test_extract_templated_secrets(test_str1)
    print(f"Secrets found: {result1}")
    print(f"Expected: []")
    print(f"✓ PASS" if result1 == [] else f"✗ FAIL")

    # Test case 2: should find legitimate secrets
    print("\n=== Test 2: Legitimate secret (should be found) ===")
    test_str2 = "${{ SECRETS.zendesk.API_KEY }}"
    print(f"Testing: {test_str2}")
    result2 = test_extract_templated_secrets(test_str2)
    print(f"Secrets found: {result2}")
    print(f"Expected: ['zendesk.API_KEY']")
    print(f"✓ PASS" if result2 == ['zendesk.API_KEY'] else f"✗ FAIL")

    # Test case 3: should find secrets but ignore those in double quotes
    print("\n=== Test 3: Mixed case with double quotes ===")
    test_str3 = "${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + \"/token:\" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}"
    print(f"Testing: {test_str3}")
    result3 = test_extract_templated_secrets(test_str3)
    result3_sorted = sorted(result3)
    print(f"Secrets found: {result3_sorted}")
    expected3 = ['zendesk.ZENDESK_API_TOKEN', 'zendesk.ZENDESK_EMAIL']
    print(f"Expected: {expected3}")
    print(f"✓ PASS" if result3_sorted == expected3 else f"✗ FAIL")

    # Test case 4: original complex test case
    print("\n=== Test 4: Complex expression (existing test) ===")
    test_str4 = "${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + '/token:' + SECRETS.zendesk.ZENDESK_API_TOKEN) }}"
    print(f"Testing: {test_str4}")
    result4 = test_extract_templated_secrets(test_str4)
    result4_sorted = sorted(result4)
    print(f"Secrets found: {result4_sorted}")
    expected4 = ['zendesk.ZENDESK_API_TOKEN', 'zendesk.ZENDESK_EMAIL']
    print(f"Expected: {expected4}")
    print(f"✓ PASS" if result4_sorted == expected4 else f"✗ FAIL")

    # Test case 5: should handle double quotes
    print("\n=== Test 5: Double quotes (should be ignored) ===")
    test_str5 = '${{ "SECRETS.test.KEY" }}'
    print(f"Testing: {test_str5}")
    result5 = test_extract_templated_secrets(test_str5)
    print(f"Secrets found: {result5}")
    print(f"Expected: []")
    print(f"✓ PASS" if result5 == [] else f"✗ FAIL")

    print("\n=== Test Summary ===")
    all_tests = [
        result1 == [],
        result2 == ['zendesk.API_KEY'],
        result3_sorted == expected3,
        result4_sorted == expected4,
        result5 == []
    ]
    passed = sum(all_tests)
    total = len(all_tests)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed\! The fix works correctly.")
    else:
        print("❌ Some tests failed.")
    
    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
