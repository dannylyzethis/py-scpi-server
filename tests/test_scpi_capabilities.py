import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import CapabilityError, PNACapabilities


def test_default_n5222b_identity_options_and_hardware_are_consistent() -> None:
    instrument = SCPIInstrument("Keysight PNA N5222B", "pna")

    assert instrument.process_command("*IDN?") == (
        "Keysight Technologies,N5222B,US12345678,A.20.25.04"
    )
    assert instrument.process_command("*OPT?") == "200"
    assert instrument.process_command("SYST:CAP:FREQ:MIN?") == "10000000"
    assert instrument.process_command("SYST:CAP:FREQ:MAX?") == "26500000000"
    assert instrument.process_command("SYST:CAP:HARD:PORT:CAT?") == "Port 1,Port 2"
    assert instrument.process_command("SYST:CAP:HARD:PORT:COUN?") == "2"
    assert instrument.process_command("SYST:CAP:HARD:SOUR:COUN?") == "1"
    assert instrument.process_command("SYST:CAP:HARD:REC:INT:COUN?") == "3"
    assert instrument.process_command("SYST:CAP:HARD:REC:DACC?") == "0"
    assert instrument.process_command("SYST:CAP:HARD:LFEX:EXIS?") == "0"
    assert instrument.process_command("SYST:CAP:LIC:CAT? VALID") == "N5222B-200"


def test_configured_options_licenses_and_feature_queries_share_one_profile() -> None:
    profile = PNACapabilities.create(
        "N5222B",
        hardware_configuration="419",
        hardware_addons=("021",),
        application_options=("S93010B", "S930902B"),
    )
    instrument = SCPIInstrument("Configured PNA", "configured", pna_capabilities=profile)

    assert instrument.process_command("*OPT?") == "419,021,010,090,902"
    assert instrument.process_command("SYST:CAP:HARD:PORT:COUN?") == "4"
    assert instrument.process_command("SYST:CAP:HARD:SOUR:COUN?") == "2"
    assert instrument.process_command("SYST:CAP:HARD:REC:INT:COUN?") == "6"
    assert instrument.process_command("SYST:CAP:HARD:REC:DACC?") == "1"
    assert instrument.process_command("SYST:CAP:HARD:ATT:REC:EXIS? 4") == "1"
    assert instrument.process_command("SYST:CAP:HARD:ATT:REC:MAX? 4") == "35"
    assert instrument.process_command("SYST:CAP:HARD:ATT:SOUR:MAX? 1") == "65"
    assert instrument.process_command("SYST:CAP:HARD:ATT:SOUR:STEP? 1") == "5"
    assert instrument.process_command("SYST:CAP:LIC:CAT? ALL") == (
        "N5222B-419,N5222B-021,S93010B-1FP,S930902B-1FP"
    )
    assert instrument.process_command("SYST:CAP:LIC:FEAT:CAT?") == (
        "Time Domain,Spectrum Analysis 26 5 Ghz"
    )
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "Time Domain"') == "1"
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "Noise Figure"') == "0"


def test_n5242b_pna_x_configuration_exposes_lfe_and_pna_x_applications() -> None:
    profile = PNACapabilities.create(
        "N5242B",
        hardware_configuration="425",
        application_options=("S93080B", "S93029B"),
    )
    instrument = SCPIInstrument("PNA-X", "pnax", pna_capabilities=profile)

    assert instrument.process_command("*OPT?") == "425,080,028"
    assert instrument.process_command("SYST:CAP:HARD:PORT:INT:COUN?") == "4"
    assert instrument.process_command("SYST:CAP:HARD:PORT:SOUR:INT:CAT?") == (
        "Port 1,Port 2,Port 3,Port 4"
    )
    assert instrument.process_command("SYST:CAP:HARD:LFEX:EXIS?") == "1"
    assert instrument.process_command("SYST:CAP:LIC:CAT? IGNORED") == ""
    assert instrument.process_command('SYST:CAP:LIC:FEAT:ENAB? "Noise Figure"') == "1"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"hardware_configuration": "999"}, "not available"),
        ({"hardware_addons": ("XSB",)}, "not available"),
        ({"application_options": ("S93029B",)}, "requires one"),
        (
            {
                "hardware_configuration": "219",
                "application_options": ("S93029B",),
            },
            "requires software",
        ),
        (
            {
                "hardware_configuration": "201",
                "application_options": ("S93088B",),
            },
            "requires four ports",
        ),
    ],
)
def test_impossible_capability_profiles_are_rejected(kwargs: dict, message: str) -> None:
    model = "N5222B" if kwargs.get("hardware_addons") == ("XSB",) else "N5242B"
    with pytest.raises(CapabilityError, match=message):
        PNACapabilities.create(model, **kwargs)


def test_capability_query_validates_port_and_license_selection() -> None:
    instrument = SCPIInstrument("Keysight N5222B", "pna")

    assert instrument.process_command("SYST:CAP:HARD:ATT:REC:EXIS? 3") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')
    assert instrument.process_command("SYST:CAP:LIC:CAT? UNKNOWN") == ""
    assert instrument.process_command("SYST:ERR?").startswith('-224,"Illegal parameter value')


def test_unaliased_software_product_uses_stable_three_digit_opt_code() -> None:
    profile = PNACapabilities.create(
        "N5222B",
        hardware_configuration="400",
        application_options=("S93072B",),
    )

    assert profile.option_query_codes == ("400", "072")
