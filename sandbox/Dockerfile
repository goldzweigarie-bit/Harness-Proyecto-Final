# Sandbox image the agent's commands run in.
# Limits (no network, CPU, memory, pids, read-only root) are applied at run time; see harness/sandbox.py.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends bash coreutils git curl jq \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 agent && mkdir /workspace && chown agent:agent /workspace
USER agent
WORKDIR /workspace

CMD ["sleep", "infinity"]
