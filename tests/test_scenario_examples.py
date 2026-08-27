from pathlib import Path

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scenario import load_scenario

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "scenarios"


def test_json_and_text_scalar_examples_drive_dmm_reads() -> None:
    instrument = SCPIInstrument("Virtual DMM", "dmm")

    instrument.attach_scenario(load_scenario(EXAMPLES / "dmm-voltage.json"))
    assert instrument.process_command("READ?") == "+3.300000000000E+00"
    assert instrument.process_command("READ?") == "+3.100000000000E+00"
    assert instrument.process_command("READ?") == "+4.800000000000E+00"
    assert instrument.process_command("READ?") == "+4.800000000000E+00"

    instrument.attach_scenario(load_scenario(EXAMPLES / "generic-readings.txt"))
    assert [instrument.process_command("READ?") for _ in range(4)] == [
        "+1.000000000000E+00",
        "+2.500000000000E+00",
        "+4.000000000000E+00",
        "+1.000000000000E+00",
    ]


def test_vna_trace_example_matches_documented_five_point_setup() -> None:
    instrument = SCPIInstrument("Virtual VNA 4 Port", "vna-4-port")
    instrument.process_command("SENS:SWE:POIN 5")
    instrument.process_command("FORM:DATA ASC")
    instrument.attach_scenario(load_scenario(EXAMPLES / "vna-s11-traces.json"))

    first = instrument.process_command("CALC:DATA? SDAT")
    second = instrument.process_command("CALC:DATA? SDAT")

    assert first == "0.1,-0.02,0.14,-0.04,0.18,-0.06,0.15,-0.03,0.11,-0.01"
    assert second.startswith("0.22,-0.08,0.27,-0.1")
    assert instrument.process_command("CALC:DATA? SDAT") == second
