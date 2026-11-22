"""Where commands run. DockerSandbox is the real one; LocalSandbox exists only for unit tests.

Interface: execute(command, timeout) -> (returncode, combined stdout+stderr), plus
copy_in(src_dir) and close(). Usable as a context manager.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass, field

TIMEOUT_RC = 124  # same code GNU `timeout` uses
OOM_RC = 137      # 128 + SIGKILL, what the kernel OOM killer leaves behind


@dataclass
class SandboxLimits:
    """Isolation settings for one container (Milestone 2)."""
    network: str = "none"          # "none" = no network at all; "bridge" to allow it
    cpus: float = 1.0
    memory: str = "512m"           # hard limit; swap disabled by setting memory-swap equal
    pids: int = 256                # stops fork bombs
    user: str = "agent"            # non-root user created in sandbox/Dockerfile
    read_only_root: bool = True    # only /workspace and /tmp are writable
    tmp_size: str = "64m"
    extra_args: list[str] = field(default_factory=list)

    def docker_args(self) -> list[str]:
        args = [
            "--network", self.network,
            "--cpus", str(self.cpus),
            "--memory", self.memory, "--memory-swap", self.memory,
            "--pids-limit", str(self.pids),
            "--user", self.user,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        ]
        if self.read_only_root:
            args += ["--read-only",
                     "--tmpfs", f"/tmp:rw,exec,size={self.tmp_size}",
                     # writable home so tools like pip --user or git config don't fail
                     "--tmpfs", f"/home/{self.user}:rw,exec,size={self.tmp_size},uid=1000,gid=1000",
                     # /workspace stays writable through an anonymous volume
                     "--mount", "type=volume,dst=/workspace"]
        return args + self.extra_args


class DockerSandbox:
    """One long-lived container per episode; each command is a `docker exec`."""

    def __init__(self, image: str = "nerv-sandbox:latest", workdir: str = "/workspace",
                 limits: SandboxLimits | None = None):
        self.image, self.workdir = image, workdir
        self.limits = limits or SandboxLimits()
        self.name = f"nerv-{uuid.uuid4().hex[:8]}"
        self._oom_seen = 0
        self.run_argv = ["docker", "run", "-d", "--rm", "--name", self.name, "-w", workdir,
                         *self.limits.docker_args(), image, "sleep", "infinity"]
        p = subprocess.run(self.run_argv, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"could not start sandbox: {p.stderr.strip()}")

    def execute(self, command: str, timeout: int = 60) -> tuple[int, str]:
        # `timeout` runs inside the container so the process dies there, not just our client:
        # SIGTERM at `timeout` seconds, SIGKILL 5 s later if it is still alive.
        argv = ["docker", "exec", "-w", self.workdir, self.name,
                "timeout", "-k", "5", str(timeout), "bash", "-lc", command]
        t0 = time.monotonic()
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 20)
        except subprocess.TimeoutExpired:
            return TIMEOUT_RC, f"[harness] command timed out after {timeout}s"
        elapsed = time.monotonic() - t0
        rc, out = p.returncode, p.stdout + p.stderr
        if rc < 0:  # killed by a signal: report it the shell way (128 + N)
            rc = 128 - rc
        if rc in (TIMEOUT_RC, OOM_RC):
            # 124/137 can also be the command's own exit code (e.g. the agent used `timeout`
            # itself), so only explain kills we can attribute.
            oom = self._oom_kills()
            if oom is not None and oom > self._oom_seen:
                self._oom_seen = oom
                out += f"\n[harness] process killed: out of memory (limit {self.limits.memory})"
            elif elapsed >= timeout:
                rc = TIMEOUT_RC
                out += f"\n[harness] command timed out after {timeout}s"
        return rc, out

    def _oom_kills(self) -> int | None:
        """oom_kill count from the container's cgroup v2 memory.events, or None if unreadable.
        The counter only grows, so comparing with the last value seen tells us about new kills."""
        q = subprocess.run(["docker", "exec", self.name, "cat", "/sys/fs/cgroup/memory.events"],
                           capture_output=True, text=True)
        for line in q.stdout.splitlines():
            if line.startswith("oom_kill "):
                return int(line.split()[1])
        return None

    def copy_in(self, src_dir: str):
        """Copy a directory's contents into /workspace, owned by the agent user.

        Streams a tar archive into `tar -x` running *as the agent*, so the files belong to
        it without needing CAP_CHOWN (which --cap-drop ALL removes, even from root)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(src_dir, arcname=".")
        p = subprocess.run(["docker", "exec", "-i", "-w", self.workdir, self.name,
                            "tar", "-x", "--no-same-owner", "-f", "-"],
                           input=buf.getvalue(), capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"copy into sandbox failed: {p.stderr.decode(errors='replace').strip()}")

    def close(self):
        subprocess.run(["docker", "rm", "-f", "-v", self.name], capture_output=True)

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
