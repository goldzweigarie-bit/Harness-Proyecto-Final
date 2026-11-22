import json
from pathlib import Path

import pytest

from harness.agent import FormatError, parse_action, run_agent
from harness.models import ScriptedModel
from harness.run import run_task
from harness.sandbox import LocalSandbox
from harness.tasks import load_task

ROOT = Path(__file__).resolve().parents[1]

FIZZBUZZ = '''I'll write the script.
```bash
cat > fizzbuzz.py <<'EOF'
import sys
for i in range(1, int(sys.argv[1]) + 1):
    print("FizzBuzz" if i % 15 == 0 else "Fizz" if i % 3 == 0 else "Buzz" if i % 5 == 0 else i)
EOF
```'''


def test_parse_single_block():
    assert parse_action("thinking\n```bash\nls -la\n```") == "ls -la"


@pytest.mark.parametrize("text", ["no block here", "```bash\nls\n```\n```bash\npwd\n```"])
def test_parse_rejects_zero_or_many(text):
    with pytest.raises(FormatError):
        parse_action(text)


def test_loop_runs_command_and_submits():
    model = ScriptedModel(["```bash\necho hello\n```", "```bash\nsubmit\n```"])
    with LocalSandbox() as sb:
        res = run_agent(model, sb, "say hello")
    assert res.exit_status == "submitted" and res.steps == 2
    assert "hello" in res.messages[2]["content"]
    assert "<returncode>0</returncode>" in res.messages[2]["content"]


def test_format_error_is_fed_back():
    model = ScriptedModel(["I forgot the block", "```bash\nsubmit\n```"])
    with LocalSandbox() as sb:
        res = run_agent(model, sb, "x")
    assert res.events[0]["format_error"] and "Format error" in res.messages[2]["content"]
    assert res.exit_status == "submitted"


def test_step_limit():
    model = ScriptedModel(["```bash\ntrue\n```"] * 10)
    with LocalSandbox() as sb:
        res = run_agent(model, sb, "loop", max_steps=3)
    assert res.exit_status == "step_limit" and res.steps == 3


def test_command_timeout():
    model = ScriptedModel(["```bash\nsleep 5\n```"])
    with LocalSandbox() as sb:
        res = run_agent(model, sb, "x", cmd_timeout=1)
    assert res.events[0]["returncode"] == 124


def test_fizzbuzz_task_end_to_end(tmp_path):
    task = load_task(ROOT / "tasks" / "001-fizzbuzz")
    model = ScriptedModel([FIZZBUZZ, "```bash\npython3 fizzbuzz.py 15\n```", "```bash\nsubmit\n```"])
    with LocalSandbox() as sb:
        traj = run_task(task, model, sb, runs_dir=tmp_path)
    assert traj["check"]["passed"], traj["check"]
    saved = json.loads(Path(traj["path"]).read_text())
    assert saved["exit_status"] == "submitted" and saved["steps"] == 3
    assert [m["role"] for m in saved["messages"]][:2] == ["user", "assistant"]


def test_check_fails_when_agent_does_nothing(tmp_path):
    task = load_task(ROOT / "tasks" / "001-fizzbuzz")
    with LocalSandbox() as sb:
        traj = run_task(task, ScriptedModel(["```bash\nsubmit\n```"]), sb, runs_dir=tmp_path)
    assert not traj["check"]["passed"]


class DyingSandbox(LocalSandbox):
    def execute(self, command, timeout=60):
        raise RuntimeError("docker daemon went away")


def test_sandbox_crash_still_saves_trajectory(tmp_path):
    task = load_task(ROOT / "tasks" / "001-fizzbuzz")
    with DyingSandbox() as sb:
        traj = run_task(task, ScriptedModel(["```bash\nls\n```"]), sb, runs_dir=tmp_path)
    assert traj["exit_status"] == "error" and "daemon went away" in traj["error"]
    assert not traj["check"]["passed"] and Path(traj["path"]).exists()
