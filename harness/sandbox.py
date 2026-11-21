"""Where commands run. DockerSandbox is the real one; LocalSandbox exists only for unit tests.

Interface: execute(command, timeout) -> (returncode, combined stdout+stderr), plus
copy_in(src_dir) and close(). Usable as a context manager.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid

TIMEOUT_RC = 124  # same code GNU `timeout` uses


class DockerSandbox:
    """One long-lived container per episode; each command is a `docker exec`.

    Milestone 2 will add --network none, --cpus/--memory and a non-root user.
    """

    def __init__(self, image: str = "nerv-sandbox:latest", workdir: str = "/workspace", docker_args: list[str] | None = None):
        self.image, self.workdir = image, workdir
        self.name = f"nerv-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", self.name, "-w", workdir,
             *(docker_args or []), image, "sleep", "infinity"],
            check=True, capture_output=True, text=True,
        )

    def execute(self, command: str, timeout: int = 60) -> tuple[int, str]:
        # `timeout` inside the container kills the process there, not just our client.
        argv = ["docker", "exec", "-w", self.workdir, self.name, "timeout", str(timeout), "bash", "-lc", command]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 10)
        except subprocess.TimeoutExpired:
            return TIMEOUT_RC, f"[harness] command timed out after {timeout}s"
        out = p.stdout + p.stderr
        if p.returncode == TIMEOUT_RC:
            out += f"\n[harness] command timed out after {timeout}s"
        return p.returncode, out

    def copy_in(self, src_dir: str):
        subprocess.run(["docker", "cp", f"{src_dir}/.", f"{self.name}:{self.workdir}"], check=True, capture_output=True)

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class LocalSandbox:
    """Runs commands in a temp dir on the host. NOT isolated: tests only."""

    def __init__(self):
        self.workdir = tempfile.mkdtemp(prefix="nerv-local-")

    def execute(self, command: str, timeout: int = 60) -> tuple[int, str]:
        try:
            p = subprocess.run(["bash", "-c", command], cwd=self.workdir, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return TIMEOUT_RC, f"[harness] command timed out after {timeout}s"
        return p.returncode, p.stdout + p.stderr

    def copy_in(self, src_dir: str):
        shutil.copytree(src_dir, self.workdir, dirs_exist_ok=True)

    def close(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
