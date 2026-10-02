import json
from importlib.resources import files

from scpi_emulator.instrument import SCPIInstrument
from scpi_emulator.scpi import (
    VNA_APPLICATION_CONTRACTS,
    VNACapabilities,
    command_spec_key,
)


def advertised_applications() -> set[str]:
    profile = files("scpi_emulator").joinpath("profiles/vna_capabilities.v1.json")
    return set(json.loads(profile.read_text(encoding="utf-8"))["applications"])


def test_every_advertised_application_has_an_explicit_behavior_contract() -> None:
    assert set(VNA_APPLICATION_CONTRACTS) == advertised_applications()
    assert {contract.kind for contract in VNA_APPLICATION_CONTRACTS.values()} <= {
        "analysis",
        "measurement",
        "modifier",
        "stimulus",
    }


def test_every_application_contract_resolves_to_an_option_gated_command() -> None:
    capabilities = VNACapabilities.create("vna-4-port")
    instrument = SCPIInstrument("Virtual VNA 4 Port", "coverage", vna_capabilities=capabilities)
    specifications = {
        command_spec_key(specification): specification
        for specification in instrument.core_registry.specifications
    }

    for application, contract in VNA_APPLICATION_CONTRACTS.items():
        assert contract.command_key in specifications, application
        assert specifications[contract.command_key].available is not None, application
