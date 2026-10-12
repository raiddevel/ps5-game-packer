#!/usr/bin/env python3
"""Fake MkPFS: prints 200,000 progress lines in about 6 s (like a big game) and a summary.
With FAKE_MKPFS_FAIL=enospc it runs out of space halfway: prints the error like the real MkPFS and exits with 1."""
import os, sys, time
if "--help" in sys.argv:
    print("--archive-root --layer"); sys.exit(0)
if "-V" in sys.argv:
    print("fake MkPFS"); sys.exit(0)
output = sys.argv[-1]
N = 200_000
t0 = time.time()
for i in range(N):
    pct = i * 100 // N
    sys.stderr.write(f"\r[{'#' * (pct // 4)}{'-' * (32 - pct // 4)}] {pct:3d}% compress 300.0 MB/s ETA {N - i}s")
    if i % 2000 == 0:
        sys.stderr.flush(); time.sleep(0.06)
    if pct >= 40 and os.environ.get("FAKE_MKPFS_FAIL") == "enospc":
        sys.stderr.write("\n"); sys.stderr.flush()
        print(f"[ERROR] [Errno 28] No space left on device: '{output}.tmp'"); sys.exit(1)
sys.stderr.write("\n"); sys.stderr.flush()
open(output, "wb").write(b"x" * 1024)
print(f"Final image size:        1.00 KB\n  Elapsed time:            {time.time() - t0:.2f}s\nSuccessfully wrote image")
