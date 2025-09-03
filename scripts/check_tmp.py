#!/usr/bin/env python3
import os
import sys

# Treats empty strings as Falsy
threshold = float(os.getenv("HEALTHCHECK_TMP_THRESHOLD") or "80")
# Allow TMP_PATH override, otherwise use env temp paths
tmp_path = os.getenv("HEALTHCHECK_TMP_PATH") or os.getenv("TMP", "/tmp")

# Check if the path exists
if not os.path.exists(tmp_path):
    print(f"Path {tmp_path} does not exist - OK")
    sys.exit(0)

# Check the disk usage
st = os.statvfs(tmp_path)
total = st.f_blocks * st.f_frsize
used = (st.f_blocks - st.f_bfree) * st.f_frsize
pct = (used / total) * 100 if total else 0
if pct < threshold:
    # OK
    print(f"Usage for {tmp_path} is {pct:.2f}% < {threshold}%")
    sys.exit(0)
else:
    # Not OK
    print(f"Usage for {tmp_path} is {pct:.2f}% >= {threshold}%")
    sys.exit(1)
