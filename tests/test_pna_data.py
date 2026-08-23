import struct
import time

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scenario import (
    AdvancePolicy,
    EndPolicy,
    ScenarioDefinition,
    ScenarioSample,
    ScenarioStream,
    StreamKind,
)
from scpi_emulator.scpi import BinaryResponse


def trace_stream(name, traces, *, advance=AdvancePolicy.READ):
    return ScenarioStream(
        name, StreamKind.TRACE, tuple(ScenarioSample(value) for value in traces),
        advance=advance, end=EndPolicy.HOLD_LAST,
    )


def instrument_with(stream) -> SCPIInstrument:
    instrument = SCPIInstrument("Virtual N5222B", "N5222B")
    instrument.process_command("SENS:SWE:POIN 2")
    instrument.attach_scenario(ScenarioDefinition("dut", (stream,), seed=19))
    return instrument


def test_sdata_reads_named_shared_trace_stream_in_ascii_and_binary() -> None:
    instrument = instrument_with(trace_stream("S11", ((1 + 2j, 3 + 4j),)))
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command("CALC:DATA? SDAT") == "1.0,2.0,3.0,4.0"

    instrument.process_command("FORM:DATA REAL,64")
    result = instrument.process_command("CALC:DATA:SDAT?")
    assert isinstance(result, BinaryResponse)
    assert struct.unpack(">4d", result.data) == (1.0, 2.0, 3.0, 4.0)


def test_fdata_applies_selected_display_transform_and_x_uses_same_point_count() -> None:
    instrument = instrument_with(trace_stream("S11", ((1 + 0j, 0 + 1j),)))
    instrument.process_command("FORM:DATA ASC")
    instrument.process_command("CALC:FORM PHAS")
    assert instrument.process_command("CALC:DATA? FDAT") == "0.0,90.0"
    assert len(instrument.process_command("CALC:MEAS:DATA:X?").split(",")) == 2


def test_late_measurement_binding_and_wrong_length_report_scpi_data_error() -> None:
    instrument = instrument_with(trace_stream("dut-gain", ((1 + 0j,),)))
    instrument.process_command('CALC:PAR:DEF:EXT "Gain","S21"')
    instrument.process_command('CALC:PAR:SEL "Gain"')
    instrument.pna_data.bind("Gain", "dut-gain")
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command("CALC:DATA? SDAT") == ""
    assert instrument.error_queue.pop().code == -230


def test_trigger_policy_advances_shared_player_used_by_pna_adapter() -> None:
    instrument = instrument_with(trace_stream(
        "S11", ((1 + 0j, 1 + 0j), (2 + 0j, 2 + 0j)), advance=AdvancePolicy.TRIGGER
    ))
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command("CALC:DATA? SDAT") == "1.0,0.0,1.0,0.0"
    instrument.process_command("INIT")
    assert instrument.process_command("CALC:DATA? SDAT") == "2.0,0.0,2.0,0.0"


def test_reset_rewinds_same_shared_player_while_cls_preserves_position() -> None:
    instrument = instrument_with(trace_stream(
        "S11", ((1 + 0j, 1 + 0j), (2 + 0j, 2 + 0j)), advance=AdvancePolicy.READ
    ))
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command("CALC:DATA? SDAT").startswith("1.0")
    instrument.process_command("*CLS")
    assert instrument.process_command("CALC:DATA? SDAT").startswith("2.0")
    instrument.process_command("*RST")
    instrument.process_command("SENS:SWE:POIN 2")
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command("CALC:DATA? SDAT").startswith("1.0")


def test_receiver_and_snp_queries_use_named_streams_and_column_order() -> None:
    instrument = SCPIInstrument("Virtual N5222B", "N5222B")
    instrument.process_command("SENS:SWE:POIN 2")
    streams = (
        trace_stream("A", ((10 + 1j, 20 + 2j),)),
        trace_stream("S11", ((1 + 0.1j, 2 + 0.2j),)),
        trace_stream("S21", ((3 + 0.3j, 4 + 0.4j),)),
        trace_stream("S12", ((5 + 0.5j, 6 + 0.6j),)),
        trace_stream("S22", ((7 + 0.7j, 8 + 0.8j),)),
    )
    instrument.attach_scenario(ScenarioDefinition("matrix", streams))
    instrument.process_command("FORM:DATA ASC")

    assert instrument.process_command("CALC:RDATA? A") == "10.0,1.0,20.0,2.0"
    values = tuple(float(value) for value in instrument.process_command(
        'CALC:DATA:SNP:PORTS? "1,2"'
    ).split(","))
    assert len(values) == 18  # 2 X values + 4 S-parameters * (2 real + 2 imaginary)
    assert values[2:6] == (1.0, 2.0, 0.1, 0.2)
    assert values[6:10] == (3.0, 4.0, 0.3, 0.4)


def test_operation_completion_policy_advances_after_sweep_finishes() -> None:
    instrument = instrument_with(trace_stream(
        "S11", ((1 + 0j, 1 + 0j), (2 + 0j, 2 + 0j)), advance=AdvancePolicy.OPERATION
    ))
    instrument.process_command("FORM:DATA ASC")
    instrument.process_command("SENS:BAND 1MHz")
    assert instrument.process_command("CALC:DATA? SDAT").startswith("1.0")
    instrument.process_command("INIT")
    deadline = time.monotonic() + 1
    while instrument.operation_manager.pending_count and time.monotonic() < deadline:
        time.sleep(0.001)
    assert instrument.operation_manager.pending_count == 0
    assert instrument.process_command("CALC:DATA? SDAT").startswith("2.0")


def test_snp_rejects_ports_not_present_in_selected_model() -> None:
    instrument = instrument_with(trace_stream("S11", ((1 + 0j, 1 + 0j),)))
    instrument.process_command("FORM:DATA ASC")
    assert instrument.process_command('CALC:DATA:SNP:PORTS? "1,3"') == ""
    assert instrument.error_queue.pop().code == -224
