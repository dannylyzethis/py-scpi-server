import json
import re
from importlib.resources import files


def load_matrix() -> dict:
    matrix_path = files("scpi_emulator").joinpath("profiles/pna_compatibility.v1.json")
    return json.loads(matrix_path.read_text(encoding="utf-8"))


def test_pna_compatibility_snapshot_is_versioned_and_pinned() -> None:
    matrix = load_matrix()

    assert matrix["schema_version"] == 1
    assert matrix["snapshot"] == {
        "date": "2026-08-20",
        "profile_revision": "1.0",
        "reference_firmware": "E.1.0",
        "firmware_release_date": "2026-07-15",
    }
    policy = matrix["compatibility_policy"]
    assert policy["verified_versions"] == [matrix["snapshot"]["reference_firmware"]]
    assert re.fullmatch(
        policy["firmware_pattern"], matrix["snapshot"]["reference_firmware"]
    )


def test_initial_model_matrix_has_physical_capabilities() -> None:
    models = load_matrix()["models"]

    assert set(models) == {"N5222B-EMU", "N5242B-EMU"}
    for model in models.values():
        assert model["frequency_hz"] == {"minimum": 10_000_000, "maximum": 26_500_000_000}
        configurations = model["hardware_configurations"]
        assert {config["ports"] for config in configurations.values()} == {2, 4}
        assert all(config["sources"] in {1, 2} for config in configurations.values())
        assert all(config["features"] for config in configurations.values())

    pna = models["N5222B-EMU"]
    assert all(
        config["sources"] == (1 if config["ports"] == 2 else 2)
        for config in pna["hardware_configurations"].values()
    )
    assert "noise_receiver" in pna["unsupported_features"]

    pna_x = models["N5242B-EMU"]
    assert {config["sources"] for config in pna_x["hardware_configurations"].values()} == {1, 2}
    assert pna_x["unsupported_features"] == []


def test_every_application_references_known_models_and_options() -> None:
    matrix = load_matrix()
    known_models = set(matrix["models"])

    assert len(matrix["applications"]) >= 20
    for application in matrix["applications"].values():
        assert set(application["models"]) <= known_models
        assert application["models"]
        assert application["options"]
        assert all(option.startswith("E9") for option in application["options"])

    assert matrix["applications"]["noise_figure"]["models"] == ["N5242B-EMU"]
    assert matrix["applications"]["active_hot_parameters"]["models"] == ["N5242B-EMU"]


def test_snapshot_uses_internal_contract_metadata_without_external_sources() -> None:
    matrix = load_matrix()

    assert matrix["snapshot"]["profile_revision"] == "1.0"
    assert "sources" not in matrix
    assert "http://" not in json.dumps(matrix)
    assert "https://" not in json.dumps(matrix)
