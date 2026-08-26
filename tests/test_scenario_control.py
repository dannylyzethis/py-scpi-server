from types import SimpleNamespace

import pytest

from scpi_emulator.emulator import HAS_FLASK, SCPIInstrument, WebDashboard


pytestmark = pytest.mark.skipif(not HAS_FLASK, reason="web extras are not installed")


class FakeServer:
    def __init__(self, instrument):
        self.instrument = instrument
        self.running = True
        self.clients = []

    def execute_control_command(self, command):
        return self.instrument.process_command(command)

    def execute_control_action(self, action):
        return action(self.instrument)


def controlled_dashboard():
    instrument = SCPIInstrument("Virtual DMM", "dmm")
    server = FakeServer(instrument)
    manager = SimpleNamespace(
        instruments={"dmm1": {"instrument": instrument, "port": 5025}},
        servers={"dmm1": server},
        web_dashboard=None,
        start_all_servers=lambda: True,
        stop_all_servers=lambda: None,
    )
    dashboard = WebDashboard(manager)
    manager.web_dashboard = dashboard
    return instrument, dashboard, dashboard.app.test_client()


def headers(dashboard):
    return {"X-SCPI-CSRF": dashboard.csrf_token}


def dmm_scenario():
    return {
        "schema_version": 1,
        "name": "remote-dut-cycle",
        "seed": 41,
        "streams": {
            "voltage.dc": {
                "kind": "scalar",
                "advance": "read",
                "end": "hold-last",
                "samples": [
                    {"value": 3.3, "label": "nominal"},
                    {"value": 4.8, "label": "overvoltage"},
                ],
            }
        },
    }


def test_remote_scenario_select_start_pause_step_reset_and_inspect() -> None:
    instrument, dashboard, client = controlled_dashboard()
    socket_client = dashboard.socketio.test_client(dashboard.app)
    socket_client.get_received()
    assert client.get("/api/session").get_json()["csrf_token"] == dashboard.csrf_token
    selected = client.put(
        "/api/scenario/dmm1",
        headers=headers(dashboard),
        json={"scenario": dmm_scenario()},
    )
    assert selected.status_code == 200
    assert selected.get_json()["scenario"]["state"] == "paused"
    assert float(instrument.process_command("READ?")) == 3.3
    assert float(instrument.process_command("READ?")) == 3.3

    started = client.post("/api/scenario/dmm1/start", headers=headers(dashboard))
    assert started.get_json()["scenario"]["state"] == "running"
    assert float(instrument.process_command("READ?")) == 3.3
    assert float(instrument.process_command("READ?")) == 4.8

    client.post("/api/scenario/dmm1/pause", headers=headers(dashboard))
    reset = client.post(
        "/api/scenario/dmm1/reset",
        headers=headers(dashboard),
        json={"seed": 99},
    )
    assert reset.get_json()["scenario"]["seed"] == 99
    stepped = client.post(
        "/api/scenario/dmm1/step",
        headers=headers(dashboard),
        json={"stream": "voltage.dc"},
    )
    assert stepped.get_json()["scenario"]["positions"][0]["index"] == 1
    status = client.get("/api/scenario/dmm1").get_json()["scenario"]
    assert status["scenario"] == "remote-dut-cycle"
    assert status["streams"][0]["index"] == 1
    state_events = [
        event["args"][0]
        for event in socket_client.get_received()
        if event["name"] == "state_changed"
    ]
    assert {event["reason"] for event in state_events} >= {
        "scenario-selected",
        "scenario-start",
        "scenario-pause",
        "scenario-reset",
        "scenario-step",
    }


def test_fault_injection_uses_the_scpi_error_and_status_system() -> None:
    instrument, dashboard, client = controlled_dashboard()
    instrument.process_command("*SRE 4")

    response = client.post(
        "/api/scenario/dmm1/fault",
        headers=headers(dashboard),
        json={"code": -222, "message": "simulated DUT overload"},
    )

    assert response.status_code == 200
    assert int(instrument.process_command("*STB?")) & 4
    assert instrument.process_command("SYST:ERR?") == (
        '-222,"Data out of range; simulated DUT overload"'
    )
    assert int(instrument.process_command("*STB?")) & 4 == 0


def test_noise_control_is_bounded_repeatable_and_part_of_scenario_playback() -> None:
    instrument, dashboard, client = controlled_dashboard()
    client.put(
        "/api/scenario/dmm1",
        headers=headers(dashboard),
        json={"scenario": dmm_scenario()},
    )

    configured = client.post(
        "/api/scenario/dmm1/noise",
        headers=headers(dashboard),
        json={"stream": "voltage.dc", "amplitude": 0.1},
    )
    first = float(instrument.process_command("READ?"))
    second = float(instrument.process_command("READ?"))
    client.post(
        "/api/scenario/dmm1/reset",
        headers=headers(dashboard),
        json={"seed": 41},
    )
    replayed = float(instrument.process_command("READ?"))

    assert configured.status_code == 200
    assert configured.get_json()["scenario"]["noise"] == {"voltage.dc": 0.1}
    assert 3.2 <= first <= 3.4
    assert second == first
    assert replayed == first

    removed = client.post(
        "/api/scenario/dmm1/noise",
        headers=headers(dashboard),
        json={"stream": "voltage.dc", "amplitude": 0},
    )
    assert removed.get_json()["scenario"]["noise"] == {}
    assert float(instrument.process_command("READ?")) == 3.3


def test_noise_control_rejects_unknown_streams_and_invalid_amplitudes() -> None:
    _, dashboard, client = controlled_dashboard()
    client.put(
        "/api/scenario/dmm1",
        headers=headers(dashboard),
        json={"scenario": dmm_scenario()},
    )

    unknown = client.post(
        "/api/scenario/dmm1/noise",
        headers=headers(dashboard),
        json={"stream": "missing", "amplitude": 1},
    )
    negative = client.post(
        "/api/scenario/dmm1/noise",
        headers=headers(dashboard),
        json={"stream": "voltage.dc", "amplitude": -1},
    )

    assert unknown.status_code == 400
    assert negative.status_code == 400


def test_invalid_control_requests_do_not_replace_or_mutate_the_scenario() -> None:
    instrument, dashboard, client = controlled_dashboard()
    client.put(
        "/api/scenario/dmm1",
        headers=headers(dashboard),
        json={"scenario": dmm_scenario()},
    )

    malformed = client.put(
        "/api/scenario/dmm1",
        headers=headers(dashboard),
        json={"scenario": {"schema_version": 1, "name": "broken", "streams": {}}},
    )
    bad_fault = client.post(
        "/api/scenario/dmm1/fault",
        headers=headers(dashboard),
        json={"code": 7},
    )
    bad_step = client.post(
        "/api/scenario/dmm1/step",
        headers=headers(dashboard),
        json={"stream": "missing"},
    )

    assert malformed.status_code == 400
    assert bad_fault.status_code == 400
    assert bad_step.status_code == 400
    assert instrument.scenario_control.inspect()["scenario"] == "remote-dut-cycle"
    assert client.get("/api/scenario/missing").status_code == 404


def test_remote_scenario_api_requires_bearer_and_csrf_tokens() -> None:
    instrument = SCPIInstrument("Virtual DMM", "dmm")
    manager = SimpleNamespace(
        instruments={"dmm1": {"instrument": instrument, "port": 5025}},
        servers={"dmm1": FakeServer(instrument)},
        web_dashboard=None,
    )
    dashboard = WebDashboard(manager, host="0.0.0.0", auth_token="remote-secret")
    manager.web_dashboard = dashboard
    client = dashboard.app.test_client()

    assert client.get("/api/session").status_code == 401
    session = client.get(
        "/api/session", headers={"Authorization": "Bearer remote-secret"}
    )
    csrf = session.get_json()["csrf_token"]
    assert client.put(
        "/api/scenario/dmm1",
        headers={"Authorization": "Bearer remote-secret"},
        json={"scenario": dmm_scenario()},
    ).status_code == 403
    assert client.put(
        "/api/scenario/dmm1",
        headers={
            "Authorization": "Bearer remote-secret",
            "X-SCPI-CSRF": csrf,
        },
        json={"scenario": dmm_scenario()},
    ).status_code == 200
