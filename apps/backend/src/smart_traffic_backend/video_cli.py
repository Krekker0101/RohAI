"""Local recorded-video workspace; camera and physical hardware are never opened."""

import argparse
import os
import signal
import threading
import time
import webbrowser

import uvicorn

from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    config = Settings(
        _env_file=None,  # type: ignore[call-arg]  # Runtime BaseSettings option.
        mode="simulation",
        hardware_mode="mock",
        operator_token=None,
        operations_enabled=False,
        operations_viewer_token=None,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config),
            host="127.0.0.1",
            port=args.port,
            timeout_graceful_shutdown=3,
        )
    )
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, lambda *_: setattr(server, "should_exit", True))
    if not args.no_browser:

        def open_browser() -> None:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not server.should_exit:
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{args.port}/#video")
                    return
                time.sleep(0.1)

        threading.Thread(target=open_browser, daemon=True).start()
    try:
        server.run()
    except KeyboardInterrupt:
        # Uvicorn re-raises SIGINT after its shutdown/lifespan cleanup completes.
        print("RohAI stopped.")


if __name__ == "__main__":
    main()
