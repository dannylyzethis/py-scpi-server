import json
from importlib.resources import files


def load_profile() -> dict:
    path = files("scpi_emulator").joinpath("profiles/vna_capabilities.v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def test_vna_capability_profile_is_versioned_and_project_owned() -> None:
    profile = load_profile()

    assert profile["schema_version"] == 1
    assert profile["snapshot"] == {
        "date": "2026-08-25",
        "profile_revision": "2.0",
        "reference_firmware": "E.1.0",
    }
    assert "http://" not in json.dumps(profile)
    assert "https://" not in json.dumps(profile)


def test_generic_models_fix_port_count_and_choose_source_defaults() -> None:
    models = load_profile()["models"]

    assert models == {
        "vna-2-port": {"ports": 2, "default_source_count": 1},
        "vna-4-port": {"ports": 4, "default_source_count": 2},
    }


def test_hardware_features_are_semantic_and_unique() -> None:
    features = load_profile()["hardware_features"]

    assert len(features) == len(set(features))
    assert {
        "direct_receiver_access",
        "noise_receiver",
        "pulse_control",
        "receiver_attenuators",
        "source_attenuators",
    } <= set(features)


def test_application_requirements_reference_known_semantic_capabilities() -> None:
    profile = load_profile()
    applications = profile["applications"]
    hardware = set(profile["hardware_features"])

    assert len(applications) >= 20
    for application, requirements in applications.items():
        assert application == application.casefold().replace("-", "_")
        assert set(requirements.get("requires_hardware", ())) <= hardware
        assert set(requirements.get("requires_applications", ())) <= set(applications)
        assert requirements.get("requires_ports", 2) in {2, 4}
        assert requirements.get("requires_sources", 1) in {1, 2}

    assert applications["source_phase_control"]["requires_ports"] == 4
    assert applications["active_hot_parameters"]["requires_sources"] == 2
    assert applications["noise_figure"]["requires_hardware"] == ["noise_receiver"]
