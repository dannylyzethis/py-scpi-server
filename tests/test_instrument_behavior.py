from queue import Empty, Queue
from threading import Thread

import pytest

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import OutputQueue


@pytest.fixture
def instrument() -> SCPIInstrument:
    device = SCPIInstrument("Test Instrument", "test_instrument")
    device.add_command("VOLT (.+)", "OK", "range:0,10")
    device.add_command("VOLT?", "5")
    device.add_command("MODE (.+)", "OK", "enum:AC,DC")
    device.add_command("MODE?", "DC")
    device.link_stateful_commands()
    return device


def test_standard_identity_and_version_are_available(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("*IDN?") == (
        "SCPI_Emulator,Test Instrument,test_instrument,E.1.0"
    )
    assert instrument.process_command("SYST:VERS?") == "1999.0"


def test_set_query_pair_persists_validated_state(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 7.5") == "OK"
    assert instrument.process_command("VOLT?") == "7.5"
    assert instrument.process_command("mode ac") == "OK"
    assert instrument.process_command("MODE?") == "AC"


def test_validation_error_is_queued_and_drained_fifo(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 11") == ""
    assert instrument.process_command("MODE INVALID") == ""

    first = instrument.process_command("SYST:ERR?")
    second = instrument.process_command("SYST:ERR?")

    assert first.startswith('-222,"Data out of range;')
    assert second.startswith('-108,"Parameter not allowed;')
    assert instrument.process_command("SYST:ERR?") == '0,"No error"'


def test_undefined_header_is_reported_through_error_queue(
    instrument: SCPIInstrument,
) -> None:
    assert instrument.process_command("NOT:A:COMMAND") == ""
    assert instrument.process_command("SYST:ERR?") == (
        '-113,"Undefined header; NOT:A:COMMAND"'
    )


def test_semicolon_chain_collects_query_responses(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("VOLT 2.5;VOLT?;SYST:VERS?") == "OK;2.5;1999.0"


def test_reset_restores_linked_query_defaults(instrument: SCPIInstrument) -> None:
    instrument.process_command("VOLT 2.5")
    assert instrument.process_command("VOLT?") == "2.5"

    assert instrument.process_command("*RST") == ""
    assert instrument.process_command("VOLT?") == "5"


def test_cls_clears_status_without_losing_values_or_command_responsiveness(
    instrument: SCPIInstrument,
) -> None:
    assert instrument.process_command("VOLT 7.5") == "OK"
    assert instrument.process_command("MODE AC") == "OK"
    assert instrument.process_command("*ESE 32") == ""
    assert instrument.process_command("*SRE 4") == ""
    assert instrument.process_command("NOT:A:COMMAND") == ""

    assert instrument.process_command("*CLS") == ""

    assert instrument.process_command("VOLT?") == "7.5"
    assert instrument.process_command("MODE?") == "AC"
    assert instrument.process_command("*IDN?").startswith("SCPI_Emulator,")
    assert instrument.process_command("SYST:ERR?") == '0,"No error"'
    assert instrument.process_command("*ESE?") == "32"
    assert instrument.process_command("*SRE?") == "4"
    assert instrument.process_command("*ESR?") == "0"
    assert instrument.process_command("*STB?") == "0"


def test_ieee_enable_register_setters_accept_parameters(instrument: SCPIInstrument) -> None:
    assert instrument.process_command("*ESE 1") == ""
    assert instrument.process_command("*ESE?") == "1"
    assert instrument.process_command("*SRE 32") == ""
    assert instrument.process_command("*SRE?") == "32"


def test_opc_event_drives_ese_esb_sre_and_master_summary(
    instrument: SCPIInstrument,
) -> None:
    assert instrument.process_command("*ESE 1") == ""
    assert instrument.process_command("*SRE 32") == ""
    sweep = instrument.begin_operation("sweep")

    assert instrument.process_command("*OPC") == ""
    assert instrument.process_command("*STB?") == "0"

    sweep.complete()

    assert instrument.process_command("*STB?") == "96"
    assert instrument.process_command("*STB?") == "96"
    assert instrument.process_command("*ESR?") == "1"
    assert instrument.process_command("*STB?") == "0"


def test_wai_blocks_later_program_units_and_abort_cancels_work(
    instrument: SCPIInstrument,
) -> None:
    sweep = instrument.begin_operation("sweep")
    responses: Queue[str] = Queue()
    thread = Thread(
        target=lambda: responses.put(instrument.process_command("*WAI;*IDN?"))
    )
    thread.start()

    with pytest.raises(Empty):
        responses.get(timeout=0.05)

    assert instrument.process_command("ABOR") == ""
    assert sweep.done
    assert responses.get(timeout=1).startswith("SCPI_Emulator,")
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert instrument.process_command("*ESR?") == "0"


def test_active_output_queue_drives_mav_and_reports_query_interruption(
    instrument: SCPIInstrument,
) -> None:
    instrument.queue_command_response("*IDN?")
    assert instrument.status.status_byte == 16
    assert instrument.read_output(5) == b"SCPI_"
    assert instrument.status.status_byte == 16

    instrument.queue_command_response("SYST:VERS?")

    assert instrument.status.event_status & 4
    assert instrument.read_output() == b"1999.0\n"
    assert instrument.status.status_byte == 4
    assert instrument.process_command("SYST:ERR?") == '-410,"Query INTERRUPTED"'
    assert instrument.status.status_byte == 0


def test_output_capacity_failure_reports_query_deadlock(
    instrument: SCPIInstrument,
) -> None:
    instrument.output_queue = OutputQueue(instrument.status, capacity=8)
    instrument.add_binary_query("CALC:DATA?", bytes(range(32)))

    assert instrument.queue_command_response("CALC:DATA?") == ""
    assert instrument.output_queue.message_available is False
    assert instrument.status.event_status & 4
    assert instrument.process_command("SYST:ERR?") == '-430,"Query DEADLOCKED"'


def test_string_parameter_case_is_preserved() -> None:
    instrument = SCPIInstrument("Test", "test")
    instrument.add_command("LABEL (.+)", "{value}")
    assert instrument.process_command("LABEL MixedCase") == "MixedCase"


def test_semicolon_inside_quoted_parameter_is_not_a_command_separator() -> None:
    instrument = SCPIInstrument("Test", "test")
    instrument.add_command("LABEL (.+)", "{value}")
    assert instrument.process_command('LABEL "A;B"') == '"A;B"'
    assert instrument.process_command('LABEL "A;B";LABEL MixedCase') == '"A;B";MixedCase'
