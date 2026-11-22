# NERV — an agent harness from scratch

Final project for *Cómputo y concurrencia*. A coding-agent harness written from
scratch in Python (standard library only): an LLM loop, a Docker sandbox, and —
in later milestones — native tool calling, sessions, context compaction,
concurrent tool calls / parallel subagents, and an automatic benchmark.

**Specialization: enterprise data and operations** — validated SQL access,
access-controlled document retrieval, log triage, and multi-agent pipelines.

Full documentation of everything built so far, design decisions and status:
[`docs/DOCUMENTATION.md`](docs/DOCUMENTATION.md).

## Layout

```
harness/      the harness
  agent.py    the loop: query model → parse one ```bash block → run → observe
  models.py   Anthropic + OpenAI-compatible providers (urllib), ScriptedModel for tests
  sandbox.py  DockerSandbox (docker run + docker exec, with limits), LocalSandbox (tests only)
  selftest.py checks against a real container that every limit is enforced
  tasks.py    task loader
  run.py      CLI: run one task, run its check, save the trajectory
sandbox/      Dockerfile for the image commands run in
tasks/        one directory per task: task.json (+ optional files/)
tests/        pytest, no network or Docker needed
runs/         trajectories (git-ignored)
```

## Quick start

```bash
docker build -t nerv-sandbox:latest sandbox/
export ANTHROPIC_API_KEY=sk-ant-...        # or OPENAI_API_KEY + --provider openai
python3 -m harness.run tasks/001-fizzbuzz
```

Output looks like:

```
001-fizzbuzz: submitted in 4 steps, 21.3s, 9120 in / 610 out tokens, check PASSED
trajectory: runs/20261115T160102Z_001-fizzbuzz/trajectory.json
```

Other providers: `--provider openai --model gpt-4.1-mini`; any OpenAI-compatible
server (OpenRouter, Ollama, vLLM) works by setting `OPENAI_BASE_URL`.

Run the tests: `python3 -m pytest -q` (no Docker needed)

Check the sandbox's isolation against real Docker (no API key needed):

```bash
python3 -m harness.selftest
```

## Sandbox isolation (Milestone 2)

Each episode gets one container started with:

| Setting | Flag | Default |
|---|---|---|
| No network | `--network none` | on (`--network bridge` to allow) |
| CPU quota | `--cpus` | 1.0 |
| Memory, no swap | `--memory` = `--memory-swap` | 512m |
| Process limit | `--pids-limit` | 256 |
| Non-root user | `--user agent` (uid 1000) | on |
| No Linux capabilities | `--cap-drop ALL`, `no-new-privileges` | on |
| Read-only root filesystem | `--read-only` + tmpfs `/tmp`, `$HOME`; volume `/workspace` | on |
| Per-command timeout | `timeout -k 5 N` inside the container | 60 s (`--timeout`) |

Override per run: `python3 -m harness.run tasks/001-fizzbuzz --memory 1g --cpus 2 --network bridge`.
The limits used are saved in each trajectory under `"limits"`.

When a command is killed, the agent is told why: a timeout comes back as exit code
124 with `[harness] command timed out after Ns`; an out-of-memory kill comes back as
137 with `[harness] process killed: out of memory`, detected from the container's
cgroup `memory.events` counter.

## How the loop works (Milestone 1)

1. The task instruction is the first user message; the system prompt asks for
   reasoning plus exactly one ```bash block per reply.
2. The harness extracts the block with a regex. Zero or several blocks → a
   format-error message goes back to the model and the step counts.
3. The command runs via `docker exec … timeout N bash -lc "<cmd>"` in a container
   that lives for the whole episode (filesystem persists, shell state does not).
4. `<returncode>` and `<output>` go back as the next user message.
5. The episode ends on `submit`, at the step limit, or on an API error.
6. The task's `check` command runs in the same container; exit 0 = pass.
7. Everything — messages, per-step timings, tokens, check result — is written to
   `runs/<timestamp>_<task>/trajectory.json`.

## Task format

```json
{
  "id": "001-fizzbuzz",
  "instruction": "what the agent should do",
  "check": "shell command; exit 0 means the task was solved",
  "max_steps": 15
}
```

## Milestones

- [x] 1. Minimal loop + API, regex action parsing, trajectory JSON
- [x] 2. Docker hardening (`--network none`, CPU/mem limits, timeouts)
- [ ] 3. Native tool calling with typed read/write/edit/bash
- [ ] 4. Sessions (JSONL/SQLite), resume, AGENTS.md
- [ ] 5. Context: output truncation, token counting, compaction
- [ ] 6. Concurrency: `asyncio.gather` tool calls, subagents + Semaphore, 429 retries
- [ ] 7. Evaluation: 15–20 auto-checked tasks; success rate, cost, time, speedup
- [ ] 8. GitHub: docker compose, Actions CI running the benchmark, demo
