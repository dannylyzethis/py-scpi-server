"""Select a DUT scenario remotely, run measurements, and reproduce a fault."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent


def request_json(url, *, token=None, csrf=None, method="GET", payload=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if csrf:
        headers["X-SCPI-CSRF"] = csrf
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    with urlopen(Request(url, data=data, headers=headers, method=method)) as response:
        return json.load(response)


def scpi(connection: socket.socket, command: str) -> str:
    connection.sendall((command + "\n").encode())
    return connection.recv(4096).decode().strip() if "?" in command else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:18081/api")
    parser.add_argument("--token")
    args = parser.parse_args()

    csrf = request_json(f"{args.api}/session", token=args.token)["csrf_token"]
    scenario = json.loads((HERE / "dut-cycle.json").read_text(encoding="utf-8"))
    request_json(
        f"{args.api}/scenario/dmm1",
        token=args.token,
        csrf=csrf,
        method="PUT",
        payload={"scenario": scenario},
    )
    request_json(
        f"{args.api}/scenario/dmm1/start",
        token=args.token,
        csrf=csrf,
        method="POST",
        payload={"reset": True},
    )
    with socket.create_connection(("127.0.0.1", 15025), timeout=2) as connection:
        print("ID:", scpi(connection, "*IDN?"))
        print("DUT readings:", [float(scpi(connection, "READ?")) for _ in range(3)])

        request_json(
            f"{args.api}/scenario/dmm1/fault",
            token=args.token,
            csrf=csrf,
            method="POST",
            payload={"code": -222, "message": "simulated DUT overvoltage"},
        )
        print("Status byte after fault:", scpi(connection, "*STB?"))
        print("Injected error:", scpi(connection, "SYST:ERR?"))
    print("Playback:", request_json(f"{args.api}/scenario/dmm1", token=args.token))


if __name__ == "__main__":
    main()
