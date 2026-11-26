"""Run one task end to end and save its trajectory.

    python -m harness.run tasks/001-fizzbuzz
    python -m harness.run tasks/001-fizzbuzz --provider openai --model gpt-4.1-mini
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .agent import run_agent
from .models import get_model
from .sandbox import DockerSandbox, LocalSandbox, SandboxLimits
from .tasks import load_task


def run_task(task, model, sandbox, runs_dir: Path = Path("runs"), cmd_timeout: int = 60) -> dict:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    if task.files_dir:
        sandbox.copy_in(str(task.files_dir))
    res = run_agent(model, sandbox, task.instruction, max_steps=task.max_steps, cmd_timeout=cmd_timeout)
    try:
        check_rc, check_out = sandbox.execute(task.check, timeout=cmd_timeout)
    except Exception as e:
        check_rc, check_out = -1, f"[harness] check could not run: {type(e).__name__}: {e}"
    traj = {
        "task_id": task.id,
        "model": getattr(model, "name", "?"),
        "sandbox": type(sandbox).__name__,
        "limits": asdict(sandbox.limits) if hasattr(sandbox, "limits") else None,
        "started_at": started.isoformat(),
        "wall_s": round(time.monotonic() - t0, 3),
        "exit_status": res.exit_status,
        "error": res.error,
        "steps": res.steps,
        "usage": res.usage,
        "check": {"command": task.check, "passed": check_rc == 0, "returncode": check_rc, "output": check_out[-2000:]},
        "events": res.events,
        "messages": res.messages,
    }
    out_dir = runs_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}_{task.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "trajectory.json").write_text(json.dumps(traj, indent=2, ensure_ascii=False))
    traj["path"] = str(out_dir / "trajectory.json")
    return traj


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run one task with the NERV harness")
    ap.add_argument("task", help="path to a task directory, e.g. tasks/001-fizzbuzz")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai", "ollama"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--image", default="nerv-sandbox:latest")
    ap.add_argument("--local", action="store_true", help="run on the host instead of Docker (unsafe, debugging only)")
    ap.add_argument("--timeout", type=int, default=60, help="per-command timeout in seconds")
    ap.add_argument("--cpus", type=float, default=1.0)
    ap.add_argument("--memory", default="512m")
    ap.add_argument("--pids", type=int, default=256)
    ap.add_argument("--network", default="none", help='"none" (default) or "bridge" to allow network')
    args = ap.parse_args(argv)

    task = load_task(args.task)
    model = get_model(args.provider, args.model)
    limits = SandboxLimits(network=args.network, cpus=args.cpus, memory=args.memory, pids=args.pids)
    sandbox = LocalSandbox() if args.local else DockerSandbox(image=args.image, limits=limits)
    with sandbox:
        traj = run_task(task, model, sandbox, cmd_timeout=args.timeout)
    u = traj["usage"]
    print(f"{task.id}: {traj['exit_status']} in {traj['steps']} steps, {traj['wall_s']}s, "
          f"{u['input_tokens']} in / {u['output_tokens']} out tokens, "
          f"check {'PASSED' if traj['check']['passed'] else 'FAILED'}")
    print(f"trajectory: {traj['path']}")
    return 0 if traj["check"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
