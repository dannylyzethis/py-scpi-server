"""Launch the example virtual bench and its authenticated control API."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from scpi_emulator.bench import BenchComposer, load_bench
from scpi_emulator.drivers import build_driver_catalog


HERE = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--dashboard-bind", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=18081)
    parser.add_argument("--token")
    args = parser.parse_args()

    bench = BenchComposer(build_driver_catalog()).compose(load_bench(HERE / "bench.json"))
    runtime = bench.start(bind_host=args.bind)
    if not runtime.start_web_dashboard(
        args.dashboard_bind,
        args.dashboard_port,
        auth_token=args.token,
    ):
        runtime.stop()
        raise SystemExit("could not start control API")
    print("SCPI resource:", bench.resources(host=args.bind)["dmm1"])
    print(f"Control API: http://{args.dashboard_bind}:{args.dashboard_port}/api")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        runtime.stop()


if __name__ == "__main__":
    main()
