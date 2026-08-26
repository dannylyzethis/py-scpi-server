from scpi_emulator.bench import (
    BenchComposer,
    BenchDefinition,
    BenchInstrument,
    ResourceAddress,
)
from scpi_emulator.drivers import POWER_SUPPLY_DRIVER_ID, build_driver_catalog


def _instrument(serial: str = "PSU-001", model: str = "ps-3-output", reported_model=None):
    definition = BenchDefinition(
        "psu-bench",
        (
            BenchInstrument(
                id="supply",
                driver=POWER_SUPPLY_DRIVER_ID,
                model=model,
                serial_number=serial,
                reported_model=reported_model,
                resource=ResourceAddress("raw-socket", "127.0.0.1", 5025),
            ),
        ),
    )
    return BenchComposer(build_driver_catalog(discover_plugins=False)).compose(
        definition
    ).instrument("supply").instrument


def test_triple_output_supply_retains_independent_selected_output_state() -> None:
    instrument = _instrument()

    assert instrument.process_command("SYST:CHAN:COUN?") == "3"
    assert instrument.process_command("INST:CAT?") == "OUT1,OUT2,OUT3"

    instrument.process_command("INST:NSEL 1")
    instrument.process_command("VOLT 5")
    instrument.process_command("CURR 0.5")
    instrument.process_command("OUTP ON")

    instrument.process_command("INST:NSEL 2")
    instrument.process_command("VOLT 12")
    instrument.process_command("CURR 1.25")
    instrument.process_command("OUTP ON")

    instrument.process_command("INST:SEL OUT3")
    instrument.process_command("VOLT 24")
    instrument.process_command("CURR 2")

    instrument.process_command("INST:NSEL 1")
    assert instrument.process_command("VOLT?") == "5.000000E+00"
    assert instrument.process_command("CURR?") == "5.000000E-01"
    assert instrument.process_command("OUTP?") == "1"
    assert instrument.process_command("MEAS:POW?") == "2.500000E+00"

    instrument.process_command("INST:NSEL 2")
    assert instrument.process_command("VOLT?") == "1.200000E+01"
    assert instrument.process_command("CURR?") == "1.250000E+00"
    assert instrument.process_command("OUTP?") == "1"
    assert instrument.process_command("MEAS:VOLT?") == "1.200000E+01"

    instrument.process_command("INST:NSEL 3")
    assert instrument.process_command("VOLT?") == "2.400000E+01"
    assert instrument.process_command("OUTP?") == "0"
    assert instrument.process_command("MEAS:VOLT?") == "0.000000E+00"


def test_each_generic_supply_profile_exposes_exactly_its_output_count() -> None:
    for output_count in range(1, 5):
        instrument = _instrument(model=f"ps-{output_count}-output")
        assert instrument.process_command("SYST:CHAN:COUN?") == str(output_count)
        assert instrument.process_command("INST:CAT?") == ",".join(
            f"OUT{number}" for number in range(1, output_count + 1)
        )
        assert instrument.process_command(f"INST:NSEL {output_count}") == ""
        assert instrument.process_command("SYST:ERR?") == '0,"No error"'
        assert instrument.process_command(f"INST:NSEL {output_count + 1}") == ""
        assert instrument.process_command("SYST:ERR?").startswith('-222,"Data out of range')


def test_cls_preserves_supply_state_and_rst_resets_every_output() -> None:
    instrument = _instrument()
    instrument.process_command("INST:NSEL 2")
    instrument.process_command("VOLT 12")
    instrument.process_command("OUTP ON")

    instrument.process_command("*CLS")
    assert instrument.process_command("INST:NSEL?") == "2"
    assert instrument.process_command("VOLT?") == "1.200000E+01"
    assert instrument.process_command("OUTP?") == "1"

    instrument.process_command("*RST")
    assert instrument.process_command("INST:NSEL?") == "1"
    for output in (1, 2, 3):
        instrument.process_command(f"INST:NSEL {output}")
        assert instrument.process_command("VOLT?") == "0.000000E+00"
        assert instrument.process_command("CURR?") == "0.000000E+00"
        assert instrument.process_command("OUTP?") == "0"


def test_supply_serial_is_unique_per_bench_instance_and_bad_output_queues_error() -> None:
    first = _instrument("PSU-001")
    second = _instrument("PSU-002")

    assert first.process_command("*IDN?") == "SCPI Emulator,Virtual PS 3 Output,PSU-001,E.1.0"
    assert second.process_command("*IDN?") == "SCPI Emulator,Virtual PS 3 Output,PSU-002,E.1.0"

    assert first.process_command("INST:NSEL 4") == ""
    assert first.process_command("SYST:ERR?").startswith('-222,"Data out of range')

    renamed = _instrument(reported_model="User Supply Model")
    assert renamed.process_command("*IDN?").split(",")[1] == "User Supply Model"


def test_one_bench_composes_two_same_model_supplies_with_unique_identity_and_ports() -> None:
    definition = BenchDefinition(
        "two-supply-bench",
        tuple(
            BenchInstrument(
                id=f"supply{number}",
                driver=POWER_SUPPLY_DRIVER_ID,
                model="ps-3-output",
                serial_number=f"PSU-00{number}",
                resource=ResourceAddress(
                    "raw-socket",
                    "127.0.0.1",
                    5024 + number,
                ),
            )
            for number in (1, 2)
        ),
    )
    composed = BenchComposer(build_driver_catalog(discover_plugins=False)).compose(definition)

    assert composed.resources() == {
        "supply1": "TCPIP::127.0.0.1::5025::SOCKET",
        "supply2": "TCPIP::127.0.0.1::5026::SOCKET",
    }
    assert composed.instrument("supply1").instrument.process_command("*IDN?").split(",")[2] == (
        "PSU-001"
    )
    assert composed.instrument("supply2").instrument.process_command("*IDN?").split(",")[2] == (
        "PSU-002"
    )
