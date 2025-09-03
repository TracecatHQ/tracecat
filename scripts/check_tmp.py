#!/usr/bin/env python3
import os
import sys

threshold = float(os.getenv("TMP_THRESHOLD", "80"))
tmp_path = os.getenv("TMP_PATH", "/tmp")
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
