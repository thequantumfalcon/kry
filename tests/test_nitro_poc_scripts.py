"""Exercise PoC shell failure/cleanup paths with inert commands, never AWS."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="Nitro parent scripts require a POSIX shell",
)

STUB = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
failures = json.loads(os.environ["NITRO_TEST_FAILURES"])
with open(os.environ["NITRO_TEST_EVENTS"], "a") as stream:
    stream.write(json.dumps({"command": name, "args": args}) + "\n")
if name == "sudo":
    sys.exit(failures.get(args[0], 0))
if name == "docker":
    sys.exit(failures.get("build", 0))
if name == "nitro-cli":
    if args[0] == "run-enclave":
        if failures.get("launch"):
            sys.exit(failures["launch"])
        print(json.dumps({"EnclaveID": "i-owned-enc-new", "EnclaveCID": 19}))
    elif args[0] == "describe-enclaves":
        print(json.dumps([{"EnclaveID": "i-unrelated-enc-old", "EnclaveCID": 20}]))
    elif args[0] == "terminate-enclave":
        sys.exit(failures.get("cleanup", 0))
if name in {"python3.11", "python3"}:
    if args[:2] == ["-m", "pip"]:
        sys.exit(failures.get("pip", 0))
    if args[0] in {"-c", "-"}:
        os.execv(sys.executable, [sys.executable, *args])
    if Path(args[0]).name == "parent_client.py":
        if failures.get("signal"):
            os.kill(os.getppid(), failures["signal"])
        sys.exit(failures.get("client", 0))
    if Path(args[0]).name == "kry_tee_verify.py":
        sys.exit(failures.get("verifier", 0))
    sys.exit(99)
if name == "pip3":
    sys.exit(failures.get("pip", 0))
if name == "unzip":
    Path("test-root.pem").write_text("constructed root; not a certificate")
'''


@pytest.fixture
def shell_run(tmp_path):
    tree = tmp_path / "copied tree" / "poc" / "nitro"
    tree.mkdir(parents=True)
    for filename in ("run_poc.sh", "setup_instance.sh"):
        shutil.copyfile(ROOT / "poc" / "nitro" / filename, tree / filename)
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("sudo", "docker", "nitro-cli", "python3", "python3.11", "pip3",
                 "curl", "unzip", "sleep"):
        script = commands / name
        script.write_text(f"#!{sys.executable}\n" + STUB)
        script.chmod(0o755)
    events_file = tmp_path / "events.jsonl"
    env = {
        "PATH": str(commands) + os.pathsep + "/usr/bin:/bin",
        "HOME": str(tmp_path), "USER": "test-operator",
        "NITRO_TEST_EVENTS": str(events_file),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(failures=None, setup_first=False):
        env["NITRO_TEST_FAILURES"] = json.dumps(failures or {})
        command = 'bash "$1/run_poc.sh"'
        if setup_first:
            command = 'bash "$1/setup_instance.sh" && ' + command
        result = subprocess.run(
            ["bash", "-c", command, "nitro-test", str(tree)], env=env,
            cwd=tmp_path, capture_output=True, text=True, timeout=20,
        )
        events = [json.loads(line) for line in events_file.read_text().splitlines()]
        return result, events

    return run


def _nitro_calls(events, subcommand):
    return [event["args"] for event in events
            if event["command"] == "nitro-cli" and event["args"][0] == subcommand]


@pytest.mark.parametrize("failures,expected_status", [
    ({}, 0),
    ({"client": 37}, 37),
    ({"verifier": 38}, 38),
    ({"cleanup": 39}, 39),
    ({"verifier": 38, "cleanup": 39}, 38),
])
def test_run_cleans_only_created_enclave_and_preserves_status(
    shell_run, failures, expected_status,
):
    result, events = shell_run(failures)
    assert result.returncode == expected_status, result.stderr
    assert len(_nitro_calls(events, "run-enclave")) == 1
    assert _nitro_calls(events, "terminate-enclave") == [
        ["terminate-enclave", "--enclave-id", "i-owned-enc-new"],
    ]
    clients = [event["args"] for event in events
               if event["args"] and event["args"][0] == "parent_client.py"]
    assert clients == [["parent_client.py", "19", "attestation.bin"]]
    if failures.get("cleanup"):
        assert "i-owned-enc-new" in result.stderr
    if failures.get("client"):
        assert not any("kry_tee_verify.py" in arg
                       for event in events for arg in event["args"])


@pytest.mark.parametrize("stage,status", [("build", 40), ("launch", 41)])
def test_pre_creation_failure_leaves_existing_enclaves_untouched(shell_run, stage, status):
    result, events = shell_run({stage: status})
    assert result.returncode == status, result.stderr
    assert _nitro_calls(events, "terminate-enclave") == []
    assert len(_nitro_calls(events, "run-enclave")) == (stage == "launch")
    assert not any("parent_client.py" in arg
                   for event in events for arg in event["args"])


@pytest.mark.parametrize("stage,status", [("dnf", 42), ("usermod", 43), ("pip", 44)])
def test_setup_failure_stops_before_enclave_creation(shell_run, stage, status):
    result, events = shell_run({stage: status}, setup_first=True)
    assert result.returncode == status, result.stderr
    assert not any(event["command"] in {"docker", "nitro-cli"} for event in events)
    if stage in {"dnf", "usermod"}:
        assert not any(event["command"] in {"pip3", "python3", "python3.11"}
                       for event in events)
    assert "setup done" not in result.stdout


@pytest.mark.parametrize("interruption,status", [
    ("SIGHUP", 129), ("SIGINT", 130), ("SIGTERM", 143),
])
def test_interruption_cleans_created_enclave_and_preserves_signal_status(
    shell_run, interruption, status,
):
    result, events = shell_run({"signal": getattr(signal, interruption)})
    assert result.returncode == status, result.stderr
    assert len(_nitro_calls(events, "run-enclave")) == 1
    assert _nitro_calls(events, "terminate-enclave") == [
        ["terminate-enclave", "--enclave-id", "i-owned-enc-new"],
    ]
    assert not any("kry_tee_verify.py" in arg
                   for event in events for arg in event["args"])
