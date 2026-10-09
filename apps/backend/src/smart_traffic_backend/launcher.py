"""One console, owned child services, readiness checks and authenticated graceful shutdown."""

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, BinaryIO

from smart_traffic_backend.maket.config import MaketConfig


class LaunchLock:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file: BinaryIO = path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        self.acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            self.acquired = True
        except OSError:
            pass

    def close(self) -> None:
        if self.acquired:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_UN)  # type: ignore[attr-defined]
        self.file.close()


def http_json(url: str, *, token: str | None = None) -> dict[str, Any]:
    headers = {"X-Operator-Token": token, "X-Launcher-Token": token} if token else {}
    request = urllib.request.Request(url, data=b"{}" if token else None, headers=headers)
    # Never send a loopback control token through an environment-configured proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        value = json.load(response)
    return dict(value)


def fingerprint() -> str:
    return hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:16]


def serve(role: str, config_path: Path, *, auto_setup: bool = True) -> None:
    import uvicorn
    from fastapi import Header, HTTPException

    config = MaketConfig.load(config_path)
    if role == "maket":
        from smart_traffic_backend.maket.app import create_maket_app

        app = create_maket_app(
            config, config_path, local_session=True, persistent_viewer=True, auto_setup=auto_setup
        )
        port = config.port
    else:
        from smart_traffic_backend.config import Settings
        from smart_traffic_backend.main import create_app

        app = create_app(Settings(mode="simulation", hardware_mode="mock"))
        port = 8000
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=3
        )
    )
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)

    @app.get("/health/launcher")
    def health() -> dict[str, Any]:
        return {
            "application": "rohai-service",
            "role": role,
            "workspace": fingerprint(),
            "ready": True,
        }

    if role == "demo":

        @app.post("/api/v1/launcher-shutdown")
        def shutdown(x_launcher_token: str = Header(default="")) -> dict[str, str]:
            if not secrets.compare_digest(x_launcher_token, os.environ["ROHAI_LAUNCHER_TOKEN"]):
                raise HTTPException(401, "Launcher authentication required")
            server.should_exit = True
            return {"status": "SHUTDOWN_REQUESTED"}

    server.run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="RohAI")
    parser.add_argument("--config", type=Path, default=Path("configs/maket/maket.json"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--serve", choices=("demo", "maket"))
    parser.add_argument(
        "--no-autosetup", action="store_true", help="Software validation only: do not probe devices"
    )
    args = parser.parse_args(argv)
    if args.serve:
        serve(args.serve, args.config, auto_setup=not args.no_autosetup)
        return 0
    if os.name == "nt":

        def interrupted(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGBREAK, interrupted)
    missing = [
        name
        for name in ("fastapi", "uvicorn", "cv2", "numpy")
        if importlib.util.find_spec(name) is None
    ]
    if sys.version_info < (3, 12) or missing:
        print(
            f"RohAI: Python 3.12+ and installed project dependencies required. Missing: {missing}"
        )
        return 2
    if not Path("apps/dashboard/dist/index.html").is_file():
        print("DEMO dashboard build missing: run npm.cmd run build in apps/dashboard once.")
        return 2
    try:
        config = MaketConfig.load(args.config)
        if config.port == 8000:
            raise ValueError("MAKET port must differ from DEMO port 8000")
    except (OSError, ValueError) as error:
        print(f"RohAI configuration error: {error}")
        return 2
    url = f"http://127.0.0.1:{config.port}/maket"
    lock = LaunchLock(Path(".runtime/rohai-launch.lock"))
    if not lock.acquired:
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    existing = http_json(f"http://127.0.0.1:{config.port}/health/launcher")
                    if (
                        existing.get("workspace") == fingerprint()
                        and existing.get("role") == "maket"
                        and existing.get("ready") is True
                    ):
                        print("RohAI is already running; reusing its verified workspace.")
                        if not args.no_browser:
                            webbrowser.open(url)
                        return 0
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            print("RohAI launch is already in progress. Check the original launcher console.")
            return 2
        finally:
            lock.close()
    children: list[tuple[subprocess.Popen[bytes], str, str]] = []
    logs: list[BinaryIO] = []
    token = secrets.token_urlsafe(32)
    environment = {**os.environ, config.operator_token_env: token, "ROHAI_LAUNCHER_TOKEN": token}
    try:
        for role, port, stop_path in (
            ("demo", 8000, "/api/v1/launcher-shutdown"),
            ("maket", config.port, "/api/maket/shutdown"),
        ):
            origin = f"http://127.0.0.1:{port}"
            try:
                http_json(origin + "/health/launcher")
            except (OSError, ValueError):
                pass
            else:
                raise RuntimeError(
                    f"Port {port} is occupied. Stop the previous service before launching."
                )
            log = Path(f".runtime/{role}-backend.log").open("wb")
            logs.append(log)
            command = [
                sys.executable,
                "-m",
                "smart_traffic_backend.launcher",
                "--serve",
                role,
                "--config",
                str(args.config.resolve()),
            ]
            if args.no_autosetup:
                command.append("--no-autosetup")
            process = subprocess.Popen(
                command,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            children.append((process, origin, stop_path))
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{role} exited. See .runtime/{role}-backend.log")
                try:
                    health = http_json(origin + "/health/launcher")
                    if (
                        health.get("workspace") == fingerprint()
                        and health.get("role") == role
                        and health.get("ready") is True
                    ):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.2)
            else:
                raise TimeoutError(f"{role} startup health check did not pass")
        print(
            f"RohAI services are ready: {url}\n"
            "Control remains stopped until checks and confirmation. Ctrl+C stops owned services."
        )
        if not args.no_browser:
            webbrowser.open(url)
        while all(child.poll() is None for child, _, _ in children):
            time.sleep(0.2)
        raise RuntimeError("A RohAI service exited unexpectedly; stopping the other owned service")
    except KeyboardInterrupt:
        print("Stopping RohAI safely…")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"RohAI startup failed: {error}")
        for role in ("demo", "maket"):
            path = Path(f".runtime/{role}-backend.log")
            if path.exists():
                print(
                    "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-12:])
                )
        return 2
    finally:
        for child, origin, stop_path in reversed(children):
            if child.poll() is None:
                try:
                    http_json(origin + stop_path, token=token)
                    child.wait(timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    print(
                        "Graceful shutdown timed out; forcing the owned process. "
                        "Device watchdog acceptance remains required."
                    )
                    child.terminate()
                    child.wait(timeout=5)
        for owned_log in logs:
            owned_log.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
