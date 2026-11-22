"""The minimal agent loop (Milestone 1).

One tool (bash), linear message history, regex action parsing.
The model replies with reasoning plus exactly one ```bash block; the harness
runs it in the sandbox and sends the output back as the next user message.
The model ends the episode by sending a block whose only content is `submit`.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

SYSTEM_PROMPT = """You are an autonomous agent working inside a Linux sandbox.
Your working directory is /workspace.

Every reply must contain your brief reasoning followed by EXACTLY ONE command
in a fenced block:

```bash
<one shell command>
```

The command runs with bash in a fresh shell (no state is kept between commands
except the filesystem). You will receive its exit code and output.
When the task is finished, reply with a block containing only:

```bash
submit
```"""

ACTION_RE = re.compile(r"```bash\s*\n(.*?)\n?```", re.DOTALL)
MAX_OUTPUT_CHARS = 10_000  # crude cap; real truncation comes in Milestone 5


class FormatError(Exception):
    pass


def parse_action(text: str) -> str:
    """Return the single bash command in a model reply, or raise FormatError."""
    blocks = ACTION_RE.findall(text)
    if len(blocks) != 1:
        raise FormatError(
            f"Format error: expected exactly one ```bash block, found {len(blocks)}. "
            "Reply again with reasoning and exactly one ```bash block."
        )
    return blocks[0].strip()


def format_observation(returncode: int, output: str) -> str:
    if len(output) > MAX_OUTPUT_CHARS:
        half = MAX_OUTPUT_CHARS // 2
        output = f"{output[:half]}\n... [{len(output) - MAX_OUTPUT_CHARS} chars truncated] ...\n{output[-half:]}"
    return f"<returncode>{returncode}</returncode>\n<output>\n{output}\n</output>"


@dataclass
class Result:
    exit_status: str  # submitted | step_limit | error
    steps: int
    messages: list[dict]
    events: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    error: str | None = None


def run_agent(model, sandbox, task: str, max_steps: int = 30, cmd_timeout: int = 60) -> Result:
    messages = [{"role": "user", "content": task}]
    res = Result(exit_status="step_limit", steps=0, messages=messages)
    for step in range(1, max_steps + 1):
        res.steps = step
        t0 = time.monotonic()
        try:
            reply, usage = model.query(SYSTEM_PROMPT, messages)
        except Exception as e:  # API failures end the run but keep the trajectory
            res.exit_status, res.error = "error", f"{type(e).__name__}: {e}"
            break
        t_model = time.monotonic() - t0
        for k in res.usage:
            res.usage[k] += usage.get(k, 0)
        messages.append({"role": "assistant", "content": reply})
        event = {"step": step, "model_s": round(t_model, 3), "usage": usage}

        try:
            command = parse_action(reply)
        except FormatError as e:
            messages.append({"role": "user", "content": str(e)})
            res.events.append({**event, "format_error": True})
            continue

        if command == "submit":
            res.exit_status = "submitted"
            res.events.append({**event, "command": "submit"})
            break

        t1 = time.monotonic()
        try:
            rc, out = sandbox.execute(command, timeout=cmd_timeout)
        except Exception as e:  # sandbox died (e.g. Docker stopped): end the run, keep the trajectory
            res.exit_status, res.error = "error", f"sandbox: {type(e).__name__}: {e}"
            res.events.append({**event, "command": command})
            break
        event.update(command=command, returncode=rc, exec_s=round(time.monotonic() - t1, 3))
        res.events.append(event)
        messages.append({"role": "user", "content": format_observation(rc, out)})
    return res
