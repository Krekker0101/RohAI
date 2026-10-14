"""Local service/browser acceptance with isolated sessions; never opens cameras or serial ports."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from smart_traffic_backend.launcher import http_json
from smart_traffic_backend.maket.config import MaketConfig
from smart_traffic_backend.maket.session import SessionRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/rohai-1.0"))
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    run = Path(".runtime") / f"acceptance-{int(time.time())}"
    run.mkdir(parents=True, exist_ok=False)
    config = MaketConfig.load(Path("configs/maket/maket.json")).model_copy(
        update={
            "camera_selected": False,
            "output_mode": "CAMERA_ONLY",
            "port": 8001,
            "session_directory": run / "sessions",
            "calibration_path": run / "calibration.json",
        }
    )
    config_path = run / "config.json"
    config_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    fixture = SessionRecorder(config)
    fixture.submit(
        "acceptance_fixture",
        {"provenance": "SYNTHETIC_REPLAY_FIXTURE", "physical_execution": False},
    )
    fixture.record_video = True
    fixture.submit_frame(
        np.full((64, 64, 3), 120, dtype=np.uint8), {"frame_id": 1, "fixture": True}
    )
    fixture.close()
    report: dict[str, object] = {
        "physical_tests": "NOT VERIFIED",
        "real_camera": "NOT VERIFIED",
        "autosetup_device_probes": "DISABLED_FOR_SOFTWARE_TEST",
        "run_directory": str(run),
    }
    log = (run / "launcher.log").open("wb")
    launcher = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "smart_traffic_backend.launcher",
            "--config",
            str(config_path),
            "--no-browser",
            "--no-autosetup",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    result = 1
    try:
        deadline = time.monotonic() + 110
        for role, port in (("demo", 8000), ("maket", 8001)):
            while time.monotonic() < deadline:
                if launcher.poll() is not None:
                    raise RuntimeError(
                        "Launcher exited before health; inspect " + str(run / "launcher.log")
                    )
                try:
                    health = http_json(f"http://127.0.0.1:{port}/health/launcher")
                    if health.get("role") == role and health.get("ready") is True:
                        report[f"{role}_health"] = "PASS"
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError("Service readiness deadline exceeded")
        duplicate = subprocess.run(
            [
                sys.executable,
                "-m",
                "smart_traffic_backend.launcher",
                "--config",
                str(config_path),
                "--no-browser",
                "--no-autosetup",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if duplicate.returncode or "already running" not in duplicate.stdout:
            raise RuntimeError("Duplicate launcher prevention failed")
        report["duplicate_prevention"] = "PASS"
        environment = {
            **os.environ,
            "ROHAI_ACCEPTANCE_LIVE": "1",
            "ROHAI_BROWSER_ARTIFACTS": str(output.resolve()),
        }
        command = (
            ["cmd.exe", "/c", "npm.cmd", "run", "test:e2e"]
            if os.name == "nt"
            else ["npm", "run", "test:e2e"]
        )
        browser = subprocess.run(
            command, cwd="apps/dashboard", env=environment, timeout=240, check=False
        )
        report["browser_exit_code"] = browser.returncode
        result = browser.returncode
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
        print(error)
    finally:
        if launcher.poll() is None:
            launcher.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                launcher.wait(timeout=40)
            except subprocess.TimeoutExpired:
                report["shutdown"] = "FAIL: launcher exceeded shutdown deadline"
                launcher.terminate()
                launcher.wait(timeout=5)
        log.close()
        report["launcher_exit_code"] = launcher.returncode
        closed = True
        for port in (8000, 8001):
            try:
                http_json(f"http://127.0.0.1:{port}/health/launcher")
                closed = False
            except (OSError, ValueError):
                pass
        report["owned_services_closed"] = closed
        if not closed or launcher.returncode != 0:
            result = 1
        (output / "workspace-acceptance.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
