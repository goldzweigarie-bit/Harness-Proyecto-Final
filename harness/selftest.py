"""Check that the Docker sandbox really enforces its limits. No model or API key needed.

    python3 -m harness.selftest
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from .sandbox import OOM_RC, TIMEOUT_RC, DockerSandbox, SandboxLimits

NET_PROBE = ("python3 -c \"import socket; s=socket.create_connection(('1.1.1.1', 53), timeout=3); print('CONN' + 'ECTED')\"")
MEM_HOG = "python3 -c \"x = b'x' * (2 * 1024**3); print('ALLOC' + 'ATED')\""  # writes 2 GiB vs 512m limit
PID_HOG = """python3 - <<'EOF'
import subprocess
ps = []
try:
    for _ in range(1000):
        ps.append(subprocess.Popen(["sleep", "30"]))
    print("spawned", len(ps))
except OSError as e:
    print("blocked after", len(ps), "processes:", e)
finally:
    for p in ps:
        p.kill()
EOF"""


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check that the Docker sandbox enforces its limits")
    ap.add_argument("--image", default="nerv-sandbox:latest")
    image = ap.parse_args(argv).image
    limits = SandboxLimits()
    print(f"sandbox self-test on {image} with {limits}")
    results = []
    with tempfile.TemporaryDirectory() as d, DockerSandbox(image=image, limits=limits) as sb:
        Path(d, "given.txt").write_text("from the task\n")
        sb.copy_in(d)

        rc, out = sb.execute("id -u")
        results.append(check("runs as non-root", rc == 0 and out.strip() != "0", f"uid {out.strip()}"))

        rc, out = sb.execute(NET_PROBE, timeout=10)
        results.append(check("no network", rc != 0 and "CONNECTED" not in out, out.strip().splitlines()[-1] if out.strip() else ""))

        rc, out = sb.execute("echo hi > note.txt && echo more >> given.txt && stat -c %U given.txt")
        results.append(check("/workspace writable, copied files owned by agent", rc == 0 and out.strip() == "agent", out.strip()))

        rc, out = sb.execute("timeout 1 sleep 5", timeout=30)
        results.append(check("agent's own exit code 124 is not mislabelled", rc == 124 and "[harness]" not in out))

        # /var/tmp is world-writable in the image, so only a read-only root makes this fail
        rc, out = sb.execute("touch /var/tmp/probe")
        results.append(check("root filesystem read-only", rc != 0 and "read-only" in out.lower(), out.strip()[:80]))

        rc, out = sb.execute("echo ok > ~/probe && cat ~/probe")
        results.append(check("$HOME writable", rc == 0 and "ok" in out))

        rc, out = sb.execute("echo ok > /tmp/t && cat /tmp/t")
        results.append(check("/tmp writable", rc == 0 and "ok" in out))

        rc, out = sb.execute(MEM_HOG, timeout=30)
        results.append(check("memory limit enforced", rc != 0 and "ALLOCATED" not in out,
                             f"rc {rc}: {out.strip().splitlines()[-1] if out.strip() else ''}"))

        t0 = time.monotonic()
        rc, out = sb.execute("sleep 30", timeout=2)
        dt = time.monotonic() - t0
        results.append(check("command timeout", rc == TIMEOUT_RC and dt < 10, f"rc {rc} after {dt:.1f}s"))

        rc, out = sb.execute("trap '' TERM; sleep 30", timeout=2)
        results.append(check("timeout kills a process that ignores SIGTERM", rc == TIMEOUT_RC, f"rc {rc}"))

        rc, out = sb.execute(PID_HOG, timeout=30)
        results.append(check("process limit enforced", "blocked after" in out,
                             out.strip().splitlines()[-1][:90] if out.strip() else f"rc {rc}"))

        rc, out = sb.execute("nproc; cat /sys/fs/cgroup/cpu.max 2>/dev/null")
        results.append(check("CPU quota set", "100000" in out, " ".join(out.split())))

    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
