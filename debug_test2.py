#\!/usr/bin/env python3

import re
import sys
sys.path.insert(0, '/workspace')

from tracecat.expressions import patterns

# Test the exact implementation from the modified file
def debug_extract_templated_secrets(templated_obj):
    secrets = set()
    
    # Same pattern as in the file - use escaped quotes properly
    quoted_string_pattern = re.compile(r"'[^']*' < /dev/null | \"[^\"]*\"")
    inner_secret_pattern = re.compile(
        r"SECRETS\.(?P<secret>[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)"
    )

    def operator(line):
        print(f"Processing line: {line}")
        for tmpl in re.finditer(patterns.TEMPLATE_STRING, line):
            expr = tmpl.group("expr")
            print(f"  Template expression: '{expr}'")
            
            # Test the pattern directly
            print(f"  Testing quoted pattern on: {repr(expr)}")
            
            # Find all quoted string ranges in the expression
            quoted_ranges = []
            for quoted_match in quoted_string_pattern.finditer(expr):
                quoted_ranges.append((quoted_match.start(), quoted_match.end()))
                print(f"    Quoted range: {quoted_match.span()}, content: {quoted_match.group()}")
            
            print(f"  Total quoted ranges found: {len(quoted_ranges)}")
            
            # Find all secret matches and filter out those inside quoted strings
            for match in re.finditer(inner_secret_pattern, expr):
                match_start, match_end = match.span()
                print(f"    Secret match at {match.span()}: {match.group()}")
                
                # Check if this match is inside any quoted string
                inside_quotes = any(
                    start <= match_start < end and start < match_end <= end
                    for start, end in quoted_ranges
                )
                
                print(f"    Inside quotes: {inside_quotes}")
                if not inside_quotes:
                    secrets.add(match.group("secret"))
                    print(f"    Added secret: {match.group('secret')}")

    if isinstance(templated_obj, str):
        operator(templated_obj)
    
    return list(secrets)

# Test the problematic case
test_str = "SECRETS.a.K1 not inside template; and ${{ 'SECRETS.a.K1' }} as string"
print(f"Testing: {test_str}")
result = debug_extract_templated_secrets(test_str)
print(f"Final result: {result}")
