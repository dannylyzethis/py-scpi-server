from types import SimpleNamespace

import pytest

from scpi_emulator.emulator import HAS_FLASK, SCPIInstrument, WebDashboard


pytestmark = pytest.mark.skipif(not HAS_FLASK, reason="web extras are not installed")


class FakeServer:
    def __init__(self, instrument, *, busy=False):
        self.instrument = instrument
        self.running = True
        self.clients = []
        self.busy = busy
        self.commands = []

    def execute_control_command(self, command):
        if self.busy:
            raise RuntimeError("instrument is busy with an active client session")
        self.commands.append(command)
        return self.instrument.process_command(command)

    def execute_control_action(self, action):
        if self.busy:
            raise RuntimeError("instrument is busy with an active client session")
        return action(self.instrument)

    def stop(self):
        self.running = False

    def start(self):
        self.running = True
        return True


def make_dashboard(*, host="127.0.0.1", token=None, busy=False):
    instrument = SCPIInstrument("<img src=x onerror=alert(1)>", "web_test")
    server = FakeServer(instrument, busy=busy)
    manager = SimpleNamespace(
        instruments={"web_test": {"instrument": instrument, "port": 5025}},
        servers={"web_test": server},
        web_dashboard=None,
        start_all_servers=lambda: True,
        stop_all_servers=lambda: None,
    )
    dashboard = WebDashboard(manager, host=host, auth_token=token)
    manager.web_dashboard = dashboard
    return dashboard, server


def mutation_headers(dashboard, *, token=None):
    headers = {"X-SCPI-CSRF": dashboard.csrf_token}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_dashboard_binds_locally_by_default_and_remote_requires_authentication() -> None:
    dashboard, _ = make_dashboard()
    assert dashboard.host == "127.0.0.1"

    with pytest.raises(ValueError, match="requires an authentication token"):
        make_dashboard(host="0.0.0.0")


def test_mutating_routes_require_csrf_and_validate_json() -> None:
    dashboard, server = make_dashboard()
    client = dashboard.app.test_client()

    assert client.post("/api/stop_all").status_code == 403
    response = client.post(
        "/api/send_command/web_test",
        headers=mutation_headers(dashboard),
        data="not json",
        content_type="text/plain",
    )
    assert response.status_code == 415
    assert server.commands == []
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_remote_control_plane_requires_bearer_token() -> None:
    dashboard, _ = make_dashboard(host="0.0.0.0", token="secret-token")
    client = dashboard.app.test_client()

    assert client.get("/api/status").status_code == 401
    assert client.get(
        "/api/status",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
    assert client.get(
        "/api/status",
        headers={"Authorization": "Bearer secret-token"},
    ).status_code == 200


def test_remote_websocket_requires_the_same_token() -> None:
    dashboard, _ = make_dashboard(host="0.0.0.0", token="secret-token")

    unauthorized = dashboard.socketio.test_client(dashboard.app)
    authorized = dashboard.socketio.test_client(
        dashboard.app,
        auth={"token": "secret-token"},
    )

    assert not unauthorized.is_connected()
    assert authorized.is_connected()
    authorized.disconnect()


def test_dashboard_command_respects_active_instrument_session() -> None:
    dashboard, server = make_dashboard(busy=True)
    response = dashboard.app.test_client().post(
        "/api/send_command/web_test",
        headers=mutation_headers(dashboard),
        json={"command": "VOLT 7"},
    )

    assert response.status_code == 409
    assert server.commands == []


def test_dashboard_scenario_mutation_respects_active_instrument_session() -> None:
    dashboard, server = make_dashboard(busy=True)
    response = dashboard.app.test_client().post(
        "/api/scenario/web_test/fault",
        headers=mutation_headers(dashboard),
        json={"code": -300, "message": "should not be queued"},
    )

    assert response.status_code == 409
    assert len(server.instrument.error_queue) == 0


def test_valid_control_command_is_serialized_and_returned_as_json() -> None:
    dashboard, server = make_dashboard()
    response = dashboard.app.test_client().post(
        "/api/send_command/web_test",
        headers=mutation_headers(dashboard),
        json={"command": "*IDN?"},
    )

    assert response.status_code == 200
    assert server.commands == ["*IDN?"]
    assert response.get_json()["response"].startswith("SCPI_Emulator,")


def test_dashboard_template_escapes_history_and_uses_text_for_live_updates() -> None:
    dashboard, _ = make_dashboard()
    html = dashboard.app.test_client().get("/").get_data(as_text=True)

    assert "escapeHtml(cmd.command)" in html
    assert "escapeHtml(cmd.response)" in html
    assert "newLine.innerHTML" not in html
    assert "message.textContent" in html
    assert "SCPI Control Room" in html
    assert "noise-apply" in html
    assert "fault-inject" in html
    assert "Channels, measurements, and traces" in html


def test_status_snapshot_is_detailed_and_non_destructive() -> None:
    dashboard, server = make_dashboard()
    instrument = server.instrument
    instrument.process_command("*ESE 32")
    instrument.process_command("*SRE 32")
    instrument.process_command("NOT:A:COMMAND")

    response = dashboard.app.test_client().get("/api/status")
    snapshot = response.get_json()["instruments"][0]["snapshot"]

    assert response.status_code == 200
    assert snapshot["status"]["event_status"] == 32
    assert snapshot["status"]["status_byte"] & 32
    assert snapshot["status"]["errors"][0]["code"] == -113
    assert snapshot["operations"]["pending_count"] == 0
    assert snapshot["scenario"]["state"] == "empty"
    assert response.get_json()["system"]["running_servers"] == 1
    assert instrument.process_command("*ESR?") == "32"
    assert instrument.process_command("SYST:ERR?").startswith('-113,"Undefined header')


def test_pna_snapshot_exposes_capabilities_channels_measurements_and_traces() -> None:
    instrument = SCPIInstrument("Virtual N5222B", "N5222B")
    server = FakeServer(instrument)
    manager = SimpleNamespace(
        instruments={"pna1": {"instrument": instrument, "port": 5025}},
        servers={"pna1": server},
        web_dashboard=None,
    )
    dashboard = WebDashboard(manager)
    manager.web_dashboard = dashboard

    snapshot = dashboard.app.test_client().get("/api/status").get_json()["instruments"][0][
        "snapshot"
    ]

    assert snapshot["capabilities"]["model"] == "N5222B"
    assert snapshot["measurements"]["channels"][0]["selected"] == "CH1_S11_1"
    assert snapshot["measurements"]["channels"][0]["measurements"][0]["parameter"] == "S11"
    assert snapshot["measurements"]["windows"][0]["traces"][0] == {
        "measurement": "CH1_S11_1",
        "number": 1,
        "title": "",
        "visible": True,
    }
