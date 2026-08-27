from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scenario import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)


def scalar_stream(values, *, advance=AdvancePolicy.READ, end=EndPolicy.ERROR):
    return ScenarioStream(
        "voltage.dc",
        StreamKind.SCALAR,
        tuple(ScenarioSample(value) for value in values),
        advance=advance,
        end=end,
    )


def dmm_with(stream) -> SCPIInstrument:
    instrument = SCPIInstrument("Virtual DMM", "dmm")
    instrument.attach_scenario(ScenarioDefinition("dut", (stream,), seed=23))
    return instrument


def test_read_sequence_models_nominal_drift_limit_failure_and_recovery() -> None:
    instrument = dmm_with(scalar_stream((3.3, 3.4, 12.0, 3.25)))
    instrument.process_command("CONF:VOLT:DC 10")

    assert instrument.process_command("READ?") == "+3.300000000000E+00"
    assert instrument.process_command("READ?") == "+3.400000000000E+00"
    assert instrument.process_command("READ?") == ""
    assert instrument.error_queue.pop().code == -222
    assert instrument.process_command("READ?") == "+3.250000000000E+00"


def test_fetch_returns_last_completed_value_without_advancing_queue() -> None:
    instrument = dmm_with(scalar_stream((1.0, 2.0), end=EndPolicy.HOLD_LAST))

    assert instrument.process_command("FETC?") == ""
    assert instrument.error_queue.pop().code == -230
    assert instrument.process_command("READ?") == "+1.000000000000E+00"
    assert instrument.process_command("FETC?") == "+1.000000000000E+00"
    assert instrument.process_command("READ?") == "+2.000000000000E+00"


def test_measure_selects_function_stream_and_explicit_binding() -> None:
    instrument = dmm_with(
        ScenarioStream(
            "dut-current", StreamKind.SCALAR, (ScenarioSample(0.125),), end=EndPolicy.HOLD_LAST
        )
    )
    instrument.scalar_data.bind("CURRent:DC", "dut-current")

    assert instrument.process_command("MEAS:CURR:DC?") == "+1.250000000000E-01"
    assert instrument.scalar_data.configuration.function == "CURRent:DC"

    instrument.attach_scenario(
        ScenarioDefinition(
            "overrange",
            (
                ScenarioStream(
                    "dut-current",
                    StreamKind.SCALAR,
                    (ScenarioSample(0.25),),
                    end=EndPolicy.HOLD_LAST,
                ),
            ),
        )
    )
    assert instrument.process_command("MEAS:CURR:DC? 0.1,1e-6") == ""
    assert instrument.error_queue.pop().code == -222


def test_trigger_and_operation_policies_use_same_generic_player() -> None:
    for policy in (AdvancePolicy.TRIGGER, AdvancePolicy.OPERATION):
        instrument = dmm_with(scalar_stream((1.0, 2.0), advance=policy, end=EndPolicy.HOLD_LAST))
        assert instrument.process_command("READ?").startswith("+1.000")
        assert instrument.process_command("READ?").startswith("+2.000")


def test_exhaustion_is_scpi_error_and_reset_rewinds_while_cls_does_not() -> None:
    instrument = dmm_with(scalar_stream((1.0, 2.0)))
    assert instrument.process_command("READ?").startswith("+1.000")
    instrument.process_command("*CLS")
    assert instrument.process_command("READ?").startswith("+2.000")
    assert instrument.process_command("READ?") == ""
    assert instrument.error_queue.pop().code == -230

    instrument.process_command("*RST")
    assert instrument.process_command("READ?").startswith("+1.000")


def test_non_dmm_csv_instruments_keep_their_legacy_read_namespace() -> None:
    supply = SCPIInstrument("Bench PSU", "psu")
    assert supply.scalar_data is None
    assert supply.process_command("READ?") == ""
    assert supply.error_queue.pop().code == -113
