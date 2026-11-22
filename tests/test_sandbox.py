"""DockerSandbox tests that need no Docker daemon: a fake `docker` on PATH records argv
and runs `exec` commands locally, with scripted cgroup OOM counters."""
import os
import stat
import textwrap

import pytest

from harness.sandbox import TIMEOUT_RC, DockerSandbox, SandboxLimits

FAKE = textwrap.dedent("""\
    #!/bin/bash
    echo "$*" >> "$FAKE_DIR/log"
    case "$1" in
      run) echo fakeid ;;
      exec)
        shift
        while [[ "$1" == -* ]]; do                 # -w DIR / -u UID / -i
          if [[ "$1" == -i ]]; then shift; else shift 2; fi
        done
        shift                                      # container name
        if [[ "$*" == "cat /sys/fs/cgroup/memory.events" ]]; then
          n=$(cat "$FAKE_DIR/oom" 2>/dev/null || echo 0); echo "oom 0"; echo "oom_kill $n"; exit 0
        fi
        mkdir -p "$FAKE_DIR/ws"; cd "$FAKE_DIR/ws"; exec "$@" ;;
    esac
""")


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    exe = tmp_path / "docker"
    exe.write_text(FAKE)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    return tmp_path


def test_default_limits_in_docker_run(fake_docker):
    with DockerSandbox() as sb:
        argv = sb.run_argv
    joined = " ".join(argv)
    for flag in ["--network none", "--cpus 1.0", "--memory 512m", "--memory-swap 512m", "--pids-limit 256",
                 "--user agent", "--cap-drop ALL", "--security-opt no-new-privileges", "--read-only"]:
        assert flag in joined, flag
    assert argv[-3:] == ["nerv-sandbox:latest", "sleep", "infinity"]
    assert "rm -f -v" in (fake_docker / "log").read_text()


def test_custom_limits():
    args = SandboxLimits(network="bridge", cpus=2, memory="1g", read_only_root=False).docker_args()
    assert args[:2] == ["--network", "bridge"] and "--read-only" not in args and "1g" in args


def test_exec_uses_in_container_timeout(fake_docker):
    with DockerSandbox() as sb:
        rc, out = sb.execute("echo hi", timeout=7)
    assert rc == 0 and "hi" in out
    assert "timeout -k 5 7 bash -lc echo hi" in (fake_docker / "log").read_text()


def test_timeout_is_reported(fake_docker):
    with DockerSandbox() as sb:
        rc, out = sb.execute("sleep 5", timeout=1)
    assert rc == TIMEOUT_RC and "timed out after 1s" in out


def test_sigterm_ignored_still_times_out(fake_docker):
    with DockerSandbox() as sb:
        rc, out = sb.execute("trap '' TERM; sleep 30", timeout=1)
    assert rc == TIMEOUT_RC and "timed out after 1s" in out


@pytest.mark.parametrize("cmd,code", [("exit 137", 137), ("timeout 1 sleep 3", 124)])
def test_own_exit_codes_are_not_mislabelled(fake_docker, cmd, code):
    with DockerSandbox() as sb:
        rc, out = sb.execute(cmd, timeout=30)
    assert rc == code and "[harness]" not in out


def test_oom_is_reported(fake_docker):
    with DockerSandbox() as sb:
        rc, out = sb.execute(f"echo 1 > {fake_docker}/oom; exit 137")
    assert rc == 137 and "out of memory (limit 512m)" in out


def test_copy_in_streams_tar_as_agent(fake_docker, tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("x")
    (src / "sub" / "b.txt").write_text("y")
    with DockerSandbox() as sb:
        sb.copy_in(str(src))
        assert sb.execute("cat a.txt sub/b.txt")[1] == "xy"
    log = (fake_docker / "log").read_text()
    assert "tar -x --no-same-owner -f -" in log
    assert "-u 0" not in log and " cp " not in log  # no root, no docker cp


def test_start_failure_is_clear(fake_docker):
    (fake_docker / "docker").write_text("#!/bin/bash\necho 'Unable to find image' >&2; exit 125\n")
    with pytest.raises(RuntimeError, match="could not start sandbox: Unable to find image"):
        DockerSandbox()
