#!/usr/bin/env python3
import os
import sys

threshold = float(os.getenv("TMP_THRESHOLD", "80"))
tmp_path = os.getenv("TMP_PATH", "/tmp")
st = os.statvfs(tmp_path)
total = st.f_blocks * st.f_frsize
used = (st.f_blocks - st.f_bfree) * st.f_frsize
pct = (used / total) * 100 if total else 0
# fail if >= threshold for healthcheck
if pct >= threshold:
    print(f"TMP usage is {pct}% which is greater than the threshold of {threshold}%")
    sys.exit(1)
else:
    print(f"TMP usage is {pct}% which is less than the threshold of {threshold}%")
    sys.exit(0)
