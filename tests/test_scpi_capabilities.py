import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import CapabilityError, CommandSpec, HeaderNode, VNACapabilities


def test_default_generic_identity_topology_and_frequency_are_consistent() -> None:
    instrument = SCPIInstrument("Virtual VNA-2PORT-EMU", "vna")

    assert instrument.process_command("*IDN?") == (
        "SCPI Emulator,VNA-2PORT-EMU,EMU00000001,E.1.0"
    )
    options = instrument.process_command("*OPT?").split(",")
    assert options[:2] == ["PORTS-2", "SOURCES-1"]
    assert "HW-NOISE-RECEIVER" in options
    assert "APP-TIME-DOMAIN" in options
    assert "APP-SOURCE-PHASE-CONTROL" not in options
    assert instrument.process_command("SYST:CAP:FREQ:MIN?") == "10000000"
    assert instrument.process_command("SYST:CAP:FREQ:MAX?") == "50000000000"
    assert instrument.process_command("SYST:CAP:HARD:PORT:CAT?") == "Port 1,Port 2"
    assert instrument.process_command("SYST:CAP:HARD:SOUR:COUN?") == "1"
    assert instrument.process_command("SYST:CAP:HARD:REC:INT:COUN?") == "3"
    assert instrument.process_command("SYST:CAP:HARD:REC:DACC?") == "1"


def test_frequency_limits_can_widen_or_narrow_without_model_ceiling() -> None:
    profile = VNACapabilities.create(
        "VNA-4PORT-EMU",
        frequency_minimum_hz=100_000,
        frequency_maximum_hz=110_000_000_000,
    )

    assert profile.frequency_minimum == 100_000
    assert profile.frequency_maximum == 110_000_000_000
    assert profile.has_low_frequency_extension is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"frequency_minimum_hz": 0}, "positive finite number"),
        ({"frequency_maximum_hz": float("inf")}, "positive finite number"),
        (
            {"frequency_minimum_hz": 20_000_000_000, "frequency_maximum_hz": 10_000_000_000},
            "cannot exceed",
        ),
        ({"frequency_minimum_hz": True}, "positive finite number"),
        ({"frequency_maximum_hz": "20GHz"}, "positive finite number"),
    ],
)
def test_invalid_frequency_limits_are_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(CapabilityError, match=message):
        VNACapabilities.create("VNA-2PORT-EMU", **kwargs)


def test_semantic_hardware_application_option_and_license_reporting() -> None:
    profile = VNACapabilities.create(
        "VNA-4PORT-EMU",
        source_count=2,
        hardware_features=(
            "direct_receiver_access",
            "receiver_attenuators",
            "source_attenuators",
        ),
        applications=("enhanced_time_domain", "spectrum_analysis"),
    )
    instrument = SCPIInstrument("Configured VNA", "configured", vna_capabilities=profile)

    assert profile.applications == (
        "enhanced_time_domain",
        "spectrum_analysis",
        "time_domain",
    )
    assert instrument.process_command("*OPT?") == (
        "PORTS-4,SOURCES-2,HW-DIRECT-RECEIVER-ACCESS,HW-RECEIVER-ATTENUATORS,"
        "HW-SOURCE-ATTENUATORS,APP-ENHANCED-TIME-DOMAIN,APP-SPECTRUM-ANALYSIS,"
        "APP-TIME-DOMAIN"
    )
    assert instrument.process_command("SYST:CAP:LIC:CAT? ALL") == (
        "APP-ENHANCED-TIME-DOMAIN,APP-SPECTRUM-ANALYSIS,APP-TIME-DOMAIN"
    )
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "APP-TIME-DOMAIN"') == "1"
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "APP-NOISE-FIGURE"') == "0"


@pytest.mark.parametrize(
    ("model", "kwargs", "message"),
    [
        ("VNA-2PORT-EMU", {"source_count": 3}, "must be 1 or 2"),
        ("VNA-2PORT-EMU", {"hardware_features": ("unknown",)}, "unknown"),
        ("VNA-2PORT-EMU", {"hardware_features": ("all", "bias_tees")}, "cannot be combined"),
        ("VNA-2PORT-EMU", {"applications": ("source_phase_control",)}, "requires 4 ports"),
        (
            "VNA-4PORT-EMU",
            {"hardware_features": (), "applications": ("noise_figure",)},
            "requires hardware features",
        ),
    ],
)
def test_impossible_generic_profiles_are_rejected(model: str, kwargs: dict, message: str) -> None:
    with pytest.raises(CapabilityError, match=message):
        VNACapabilities.create(model, **kwargs)


def test_omitted_applications_enable_every_compatible_application() -> None:
    two_port = VNACapabilities.create("VNA-2PORT-EMU")
    four_port = VNACapabilities.create("VNA-4PORT-EMU")

    assert two_port.feature_enabled("APP-NOISE-FIGURE") is True
    assert two_port.feature_enabled("source-phase-control") is False
    assert four_port.feature_enabled("source-phase-control") is True
    assert four_port.feature_enabled("active hot parameters") is True


def test_capability_query_validates_port_and_license_selection() -> None:
    instrument = SCPIInstrument("Virtual VNA-2PORT-EMU", "vna")

    assert instrument.process_command("SYST:CAP:HARD:ATT:REC:EXIS? 3") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    assert instrument.process_command("SYST:CAP:LIC:CAT? UNKNOWN") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')


def test_vna_application_capabilities_gate_typed_commands() -> None:
    base = SCPIInstrument(
        "Base VNA",
        "base",
        vna_capabilities=VNACapabilities.create("VNA-4PORT-EMU", applications=()),
    )
    full = SCPIInstrument("Full VNA-4PORT-EMU", "full")
    specification = CommandSpec(
        path=(HeaderNode("CALCulate"), HeaderNode("NOISe")),
        handler=lambda invocation: "1",
        query=True,
        required_capabilities=frozenset({"noise-figure"}),
    )
    base.core_registry.register(specification)
    full.core_registry.register(specification)

    assert base.process_command("CALC:NOIS?") == ""
    assert base.process_command("SYST:ERR?").startswith('-113,"Command unavailable')
    assert full.process_command("CALC:NOIS?") == "1"


def test_unknown_model_lists_the_generic_choices() -> None:
    with pytest.raises(CapabilityError, match="VNA-2PORT-EMU, VNA-4PORT-EMU"):
        VNACapabilities.create("unknown-vna")
