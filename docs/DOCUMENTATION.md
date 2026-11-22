# NERV — Project documentation

A coding-agent harness written from scratch in Python, specialized for **enterprise
data and operations**. Final project for *Cómputo y concurrencia*.

This file records what has been built, why each decision was made, how it was
verified, and what is still open. It is updated at the end of every milestone.

**Author:** Arié Goldzweig

---

## Contents

1. [Specialization: enterprise data and operations](#1-specialization-enterprise-data-and-operations)
2. [Status at a glance](#2-status-at-a-glance)
3. [Architecture](#3-architecture)
4. [Milestone 1 — Minimal agent loop](#4-milestone-1--minimal-agent-loop)
5. [Milestone 2 — Sandbox isolation](#5-milestone-2--sandbox-isolation)
6. [Verification](#6-verification)
7. [Design decisions](#7-design-decisions)
8. [Known limitations](#8-known-limitations)
9. [Roadmap](#9-roadmap)
10. [How to run](#10-how-to-run)
11. [Changelog](#11-changelog)
12. [References](#12-references)

---

## 1. Specialization: enterprise data and operations

An agent harness is the software around a language model that turns its text
into actions: it calls the model, runs the tools the model asks for, enforces
limits, and feeds the results back. A general coding harness only needs a shell.
A harness for a business environment also has to answer *who* the agent is acting
for and *what data that identity may touch*.

Google Cloud's overview of agent harnesses (last updated 15 Oct 2026) describes
four enterprise patterns, which define this project's specialization:

| Pattern (Google Cloud) | What it means | How NERV will implement it |
|---|---|---|
| **SQL validation** | The harness checks the SQL the model generates and enforces the user's identity boundaries before results go back to the model. | A `sql_query` tool over a synthetic company database (SQLite, standard library). Read-only connection, statement allow-list (single `SELECT`/`WITH` only), `EXPLAIN` before execution, row and time limits, per-identity table/column permissions. |
| **Access-controlled document retrieval** | When the agent searches company documents, it only retrieves what the requesting user is authorized to see. | A `search_docs` tool over a document store where every document carries an access-control list. Filtering happens in the harness, before results reach the model, so the model never sees unauthorized text. |
| **Log triage and monitoring** | Multi-step diagnosis of error spikes and log traces, suggesting remediation within guardrails. | Tasks with synthetic application logs and alerts. The agent investigates and writes a triage report; the harness enforces "suggest, don't apply" by keeping production-like resources read-only. |
| **Multi-agent pipelines** | The harness manages state hand-offs between specialized agents working on one business task. | Milestone 6: a coordinator agent that delegates to parallel sub-agents (e.g. one per log source or per data question), with a concurrency limit and merged results. |

Cross-cutting requirement: every tool call is recorded with the identity it ran
as, so a run can be audited afterwards (Milestone 4).

All data used is **synthetic**. No real company or customer data goes into the
repository, the tasks or the trajectories.

> Sections 4–5 describe what exists today. The enterprise tools above are
> **planned** (Section 9); none of them is implemented yet.

---

## 2. Status at a glance

| Milestone | Code | Tests (no Docker) | Verified on real Docker | Committed |
|---|---|---|---|---|
| 1. Minimal loop, regex parsing, trajectory JSON | done | 10 pass | **pending** — first real run with an API key not done yet | yes (`4d55225`) |
| 2. Sandbox isolation (network, CPU, memory, pids, read-only, timeouts) | done | 10 pass | **pending** — `harness.selftest` not run yet | no |
| 3–8 | not started | — | — | — |

A milestone counts as **done** only when its code is committed *and* it has been
verified on real Docker (Section 6).

---

## 3. Architecture

```
          ┌──────────────── harness (host, Python) ────────────────────┐
 task ──▶ │ messages ──▶ model API ──▶ reply ──▶ parse one ```bash``` │
          │     ▲                                     │                │
          │     └─── observation (exit code, output) ◀┤                │
          └───────────────────────────────────────────┼────────────────┘
                                                      ▼ docker exec
                                       ┌──── container (isolated) ────┐
                                       │ bash runs the command        │
                                       └──────────────────────────────┘
```

| Path | Role |
|---|---|
| `harness/agent.py` | The loop. Independent of the model provider and of the sandbox. |
| `harness/models.py` | Anthropic and OpenAI-compatible API clients (urllib), plus `ScriptedModel` for tests. |
| `harness/sandbox.py` | `DockerSandbox` (real, isolated) and `LocalSandbox` (tests only, not isolated). |
| `harness/tasks.py` | Loads a task from `tasks/<id>/`. |
| `harness/run.py` | Command-line runner: one task → agent → check → `trajectory.json`. |
| `harness/selftest.py` | Checks the sandbox's isolation against a real container. |
| `sandbox/Dockerfile` | The image commands run in. |
| `tasks/` | One folder per task: `task.json` and optional `files/`. |
| `tests/` | pytest suite; needs neither network nor Docker. |
| `runs/` | Trajectories (git-ignored). |

The loop is **sequential**: one model call, then one command, then the next model
call. This is the baseline that Milestone 6's concurrent tool calls and parallel
sub-agents are measured against.

The harness has **no third-party dependencies**; it uses only the Python standard
library. `pytest` is needed only to run the tests.

---

## 4. Milestone 1 — Minimal agent loop

**Goal:** a model can solve a task by running shell commands in a container, and
every run leaves a complete, machine-readable record.

### 4.1 The protocol (`harness/agent.py`)

The system prompt tells the model it works in `/workspace` inside a Linux
sandbox, that each reply must contain brief reasoning plus **exactly one**
fenced ```` ```bash ```` block, that each command runs in a fresh shell (only
the filesystem persists), and that it finishes by sending a block containing only
`submit`. This follows mini-SWE-agent: one tool, actions parsed from text.

- **Parsing** — regex ```` ```bash\s*\n(.*?)\n?``` ```` with `re.DOTALL`.
  Exactly one block is accepted; zero or several produce a format error.
- **Format errors** go back to the model as a user message and count as a step,
  so a model that never complies ends at the step limit instead of looping.
- **Observations** are returned as
  `<returncode>N</returncode>` and `<output>…</output>`. Output over 10,000
  characters keeps its first and last 5,000 characters.
- **Termination** — `submitted` (model sent `submit`), `step_limit`, or `error`
  (API failure after retries, or the sandbox died).
- The message history is linear and alternates user/assistant, as the Anthropic
  API requires.

### 4.2 Model providers (`harness/models.py`)

Interface: `query(system, messages) -> (text, {"input_tokens", "output_tokens"})`.

- `AnthropicModel` — `POST /v1/messages`; default model `claude-sonnet-5-5`.
- `OpenAICompatModel` — `POST {base_url}/chat/completions`; works with OpenAI,
  OpenRouter, Ollama, vLLM via `OPENAI_BASE_URL`.
- Retries on HTTP 429, 500, 502, 503, 529: honours `retry-after`, otherwise
  exponential back-off (1, 2, 4, 8 s, max 30 s), up to 4 retries.
- `ScriptedModel` replays canned replies (tests, sandbox checks without tokens).

### 4.3 Tasks (`harness/tasks.py`, `tasks/`)

```json
{ "id": "001-fizzbuzz", "instruction": "...", "check": "<shell command>", "max_steps": 15 }
```

The `check` runs in the same container after the agent stops; exit code 0 means
solved. The agent never sees it. Task `001-fizzbuzz` checks the output for N=15
exactly and also N=1, so hard-coding the expected output fails.

### 4.4 Runs and trajectories (`harness/run.py`)

`python3 -m harness.run tasks/<id>` copies the task's files in, runs the agent,
runs the check and writes `runs/<UTC timestamp>_<task>/trajectory.json`:

| Field | Content |
|---|---|
| `task_id`, `model`, `sandbox`, `limits` | What ran, where, under which limits |
| `started_at`, `wall_s` | Start time (UTC) and total seconds |
| `exit_status`, `error`, `steps` | How it ended |
| `usage` | Input/output tokens |
| `check` | `passed`, return code, last 2,000 characters of output |
| `events` | Per step: command, return code, model seconds, execution seconds, format errors |
| `messages` | Full conversation |

The process exits 0 if the check passed and 1 otherwise, so CI can use it
directly. The sandbox is always removed, including after errors or Ctrl-C.

---

## 5. Milestone 2 — Sandbox isolation

**Goal:** the model's commands are untrusted code. One command must not be able
to reach the network, exhaust the host, or change anything outside its work
area — and when a command is stopped, the agent is told why.

Isolation relies on two Linux kernel mechanisms: **namespaces** (what a process
can see: filesystem, processes, network, users) and **cgroups** (how much it can
use: CPU, memory, processes).

### 5.1 Container lifecycle

```
docker run -d --rm --name nerv-<random> -w /workspace <limits> nerv-sandbox:latest sleep infinity
docker exec -w /workspace nerv-… timeout -k 5 <N> bash -lc "<command>"     # once per step
docker rm -f -v nerv-…                                                     # always, at the end
```

One container per episode (files persist between steps; starting a container per
command would add ~0.5–1 s each). Each command gets a fresh `bash -lc`. Random
names allow parallel runs in Milestone 6.

### 5.2 Limits (`SandboxLimits`)

| Setting | Docker flag | Default | Purpose |
|---|---|---|---|
| No network | `--network none` | on | No exfiltration, downloads or LAN access. The API is called from the host. |
| CPU | `--cpus` | 1.0 | A runaway loop cannot freeze the host. |
| Memory, no swap | `--memory` = `--memory-swap` | 512m | Over the limit the kernel's OOM killer stops the process; no swapping. |
| Processes | `--pids-limit` | 256 | Stops fork bombs. |
| Non-root | `--user agent` (uid 1000) | on | Limits damage from an escape. |
| No capabilities | `--cap-drop ALL`, `--security-opt no-new-privileges` | on | Even root inside has no special powers; `setuid` cannot raise privileges. |
| Read-only root | `--read-only` + tmpfs `/tmp`, `/home/agent` + volume `/workspace` | on | Only the work area and scratch space are writable. |
| Command timeout | `timeout -k 5 N` inside the container | 60 s | SIGTERM at N s, SIGKILL 5 s later. |

All limits can be changed per run (`--cpus --memory --pids --network --timeout`)
and are saved in the trajectory.

### 5.3 Telling the agent why a command stopped

| What happened | Exit code | How it is identified | The agent sees |
|---|---|---|---|
| Normal exit | any | — | unchanged |
| Ran past N s | 124 | elapsed ≥ N | 124 + `command timed out after Ns` |
| Ignored SIGTERM, killed at N+5 s | 137 | elapsed ≥ N | rewritten to 124 + `timed out` |
| Out of memory | 137 | cgroup `memory.events` `oom_kill` counter increased | 137 + `process killed: out of memory (limit 512m)` |
| The agent's own `timeout`/`kill -9` | 124/137 | elapsed < N, no OOM | unchanged (no false label) |

### 5.4 Copying task files in

Files are streamed as a tar archive into `tar -x` running **as the agent user**,
so they are owned by it without needing `CAP_CHOWN`, which `--cap-drop ALL`
removes even from root. (`docker cp` + `chown`, the first approach, would have
failed under these limits.)

### 5.5 Self-test (`python3 -m harness.selftest`)

Twelve checks against a real container, no API key needed: non-root; no network;
`/workspace` writable and copied files owned by `agent`; the agent's own exit
code 124 not mislabelled; root filesystem read-only (probed on world-writable
`/var/tmp`); `$HOME` writable; `/tmp` writable; memory limit (writes 2 GiB);
command timeout; SIGTERM-ignoring process still stopped; process limit; CPU quota.

---

## 6. Verification

| Evidence | Proves | Status |
|---|---|---|
| `pytest` — 20 tests (10 loop, 10 sandbox) | Loop logic; that the harness requests the right Docker flags and interprets exit codes correctly (uses a fake `docker` command) | passing |
| `python3 -m harness.selftest` | That Docker actually enforces each limit | **pending** |
| `python3 -m harness.run tasks/001-fizzbuzz` with a real model | The full loop end to end; a saved trajectory | **pending** |

The unit tests deliberately do not prove isolation — only the self-test on a
real container does.

Results of the pending checks will be recorded here, with date, Docker version
and the self-test output.

---

## 7. Design decisions

| Decision | Alternatives | Reason |
|---|---|---|
| Standard library only | Anthropic/OpenAI SDKs, LangChain | Every line is explainable; clone-and-run with no installs. |
| One bash tool, parsed from text (M1) | Native tool calling from the start | Smallest working loop; native tools come in M3. |
| Exactly one action per reply | Run every block | One step = one action keeps the loop and its timing analysis simple. |
| Hidden check run after the agent | Ask the model whether it succeeded | Success is measured, not self-reported. |
| One container per episode, `docker exec` per command | New container per command | Filesystem persists; avoids ~1 s start-up per step. |
| Fresh shell per command | Persistent shell session | Predictable state; no hidden leftovers between steps. |
| Timeout inside the container | Kill the `docker exec` client | Killing the client leaves the process running in the container. |
| Kill reasons from elapsed time and cgroup OOM counter | Exit code alone | 124/137 are ambiguous; the agent's own commands can produce them. |
| Tar stream as agent for copy-in | `docker cp` + `chown` | `chown` is impossible with all capabilities dropped. |
| Synthetic data only | Real business data | No confidential data in a public repository. |

---

## 8. Known limitations

- Isolation and the full loop are not yet verified on real Docker (Section 6).
  Two details depend on the Docker host: the `uid=` option on the `/home/agent`
  tmpfs, and whether `memory.events` is readable inside the container (if not,
  out-of-memory kills still return 137, without the explanation).
- A command that itself contains ```` ``` ```` breaks text parsing (fixed by
  native tool calling in M3).
- Network-level API errors (DNS, dropped connection) are not retried (M6).
- Output truncation is by characters; there is no token budget (M5).
- Token counts are recorded, cost is not (M7).
- The agent and the check share a container; a malicious agent could tamper with
  what the check relies on.
- No identity or permission model yet — required by the specialization (M3–M4).

---

## 9. Roadmap

Original estimates in hours; specialization items added.

| # | Milestone | Enterprise specialization | h |
|---|---|---|---|
| 1 | Minimal loop, regex parsing, trajectory JSON | — | 6 |
| 2 | Sandbox isolation | Guardrails for operational tasks | 5 |
| 3 | Native tool calling: typed `read`, `write`, `edit`, `bash` | Add `sql_query` (validated, read-only, per-identity permissions) and `search_docs` (ACL-filtered) | 8 + |
| 4 | Sessions (JSONL/SQLite), resume, `AGENTS.md` | Identity per run; audit log of every tool call with the identity it ran as | 5 + |
| 5 | Context: output truncation, token counting, compaction | — | 6 |
| 6 | Concurrency: `asyncio.gather` tool calls, sub-agents + semaphore, 429 retries | Multi-agent pipeline (coordinator + parallel investigators) for log triage | 10 |
| 7 | Evaluation: 15–20 auto-checked tasks; success rate, cost, time, speedup | Task suite: SQL questions on a synthetic company DB, ACL leak tests (pass = nothing unauthorized revealed), log triage | 8 + |
| 8 | GitHub: docker compose, Actions CI running the benchmark, README, demo | — | 6 |

---

## 10. How to run

```bash
docker build -t nerv-sandbox:latest sandbox/
python3 -m harness.selftest                     # isolation checks, no API key
python3 -m pytest -q                            # unit tests, no Docker
export ANTHROPIC_API_KEY=...                    # or OPENAI_API_KEY with --provider openai
python3 -m harness.run tasks/001-fizzbuzz
```

---

## 11. Changelog

| Date | Change |
|---|---|
| 2026-11-15 | Milestone 1: loop, providers, Docker sandbox, task format, trajectories, 9 tests. Committed. |
| 2026-11-15 | Milestone 2 written: limits, kill reasons, self-test. Review fixes: copy-in via tar as agent user (the `chown` approach fails without capabilities); kill labels only when attributable; read-only probe moved to `/var/tmp`; OOM counter read only after a kill; sandbox crash still saves the trajectory. 20 tests. Not yet committed. |
| 2026-11-21 | Specialization chosen: enterprise data and operations. This documentation file added. |

---

## 12. References

- Google Cloud, *What is an agent harness?* — https://cloud.google.com/discover/agent-harness
- mini-SWE-agent — https://github.com/SWE-agent/mini-swe-agent
- M. Zechner, *Pi coding agent* — https://www.mariozechner.at/posts/2025-11-30-pi-coding-agent/
- Arize, *How Hermes implements an open-source agent harness architecture* — https://arize.com/blog/how-hermes-implements-open-source-agent-harness-architecture/
- Prime Intellect, *Prime Agent* — https://primeintellect.ai/blog/prime-agent
- Amp, *How to build an agent* — https://ampcode.com/how-to-build-an-agent
- Course page: *Secuencial, concurrente, paralelo* — https://rayalucaria.org/fdd_o26/computo-y-concurrencia/secuencial-concurrente-paralelo/index.html
