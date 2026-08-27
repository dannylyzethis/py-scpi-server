from time import monotonic, sleep

from scpi_emulator.scpi import (
    AcquisitionController,
    AcquisitionState,
    CommandRegistry,
    OperationConditionBit,
    OperationManager,
    OperationState,
    StandardEventBit,
    StatusByteBit,
    StatusSystem,
    SweepMode,
    TriggerSource,
    parse_program_message,
    register_acquisition_commands,
    register_operation_commands,
    register_status_commands,
)


def controller(*, auto_progress: bool = False):
    status = StatusSystem()
    operations = OperationManager(status)
    acquisition = AcquisitionController(operations, status, auto_progress=auto_progress)
    return acquisition, operations, status


def dispatch(registry: CommandRegistry, source: str):
    return registry.dispatch(parse_program_message(source).commands[0])


def test_external_trigger_runs_complete_state_sequence() -> None:
    acquisition, _, _ = controller()
    acquisition.set_trigger_source(TriggerSource.EXTERNAL, 2)

    operation = acquisition.initiate(2)

    channel = acquisition.channel(2)
    assert channel.state is AcquisitionState.WAITING
    assert operation.state is OperationState.PENDING
    assert acquisition.manual_trigger(2) == 0
    assert acquisition.external_trigger(2) == 1
    assert channel.state is AcquisitionState.SWEEPING

    acquisition.complete_sweep(2)
    assert channel.state is AcquisitionState.PROCESSING
    acquisition.complete_processing(2)

    assert channel.state is AcquisitionState.COMPLETE
    assert operation.state is OperationState.COMPLETED


def test_trigger_delay_keeps_channel_waiting_until_delay_elapses() -> None:
    acquisition, _, _ = controller()
    acquisition.set_trigger_source(TriggerSource.MANUAL, 1)
    acquisition.set_trigger_delay(0.5, 1)
    acquisition.initiate(1)

    assert acquisition.manual_trigger(1) == 1
    assert acquisition.channel(1).state is AcquisitionState.WAITING
    assert acquisition.channel(1).trigger_received is True

    acquisition.delay_elapsed(1)
    assert acquisition.channel(1).state is AcquisitionState.SWEEPING


def test_bus_trigger_can_start_multiple_waiting_channels() -> None:
    acquisition, _, _ = controller()
    for number in (1, 2):
        acquisition.set_trigger_source(TriggerSource.BUS, number)
        acquisition.initiate(number)

    assert acquisition.bus_trigger() == 2
    assert acquisition.channel(1).state is AcquisitionState.SWEEPING
    assert acquisition.channel(2).state is AcquisitionState.SWEEPING


def test_averaging_requires_configured_number_of_acquisitions() -> None:
    acquisition, _, _ = controller()
    acquisition.set_averaging(1, True)
    acquisition.set_averaging_count(1, 2)
    operation = acquisition.initiate(1)

    acquisition.complete_sweep(1)
    acquisition.complete_processing(1)

    assert acquisition.channel(1).averages_completed == 1
    assert acquisition.channel(1).state is AcquisitionState.SWEEPING
    assert operation.state is OperationState.PENDING

    acquisition.complete_sweep(1)
    acquisition.complete_processing(1)

    assert acquisition.channel(1).averages_completed == 2
    assert acquisition.channel(1).state is AcquisitionState.COMPLETE
    assert operation.state is OperationState.COMPLETED


def test_group_sweep_mode_completes_only_after_group_count() -> None:
    acquisition, _, _ = controller()
    acquisition.set_sweep_mode(1, SweepMode.GROUPS)
    acquisition.set_group_count(1, 2)
    operation = acquisition.initiate(1)

    acquisition.complete_sweep(1)
    acquisition.complete_processing(1)
    assert acquisition.channel(1).state is AcquisitionState.SWEEPING
    assert operation.state is OperationState.PENDING

    acquisition.complete_sweep(1)
    acquisition.complete_processing(1)
    assert acquisition.channel(1).state is AcquisitionState.COMPLETE
    assert operation.state is OperationState.COMPLETED


def test_acquisition_states_drive_operation_condition_bits() -> None:
    acquisition, _, status = controller()
    acquisition.set_trigger_source(TriggerSource.EXTERNAL, 1)
    acquisition.initiate(1)
    assert status.operation.condition == OperationConditionBit.WAITING_FOR_TRIGGER

    acquisition.external_trigger(1)
    assert status.operation.condition == int(
        OperationConditionBit.SWEEPING | OperationConditionBit.MEASURING
    )

    acquisition.complete_sweep(1)
    assert status.operation.condition == OperationConditionBit.MEASURING

    acquisition.complete_processing(1)
    assert status.operation.condition == 0
    assert status.operation.event & OperationConditionBit.WAITING_FOR_TRIGGER
    assert status.operation.event & OperationConditionBit.SWEEPING
    assert status.operation.event & OperationConditionBit.MEASURING


def test_global_abort_marks_channels_aborted_and_clears_operation_condition() -> None:
    acquisition, operations, status = controller()
    operation = acquisition.initiate(1)

    operations.abort()

    assert acquisition.channel(1).state is AcquisitionState.ABORTED
    assert operation.state is OperationState.CANCELLED
    assert status.operation.condition == 0


def test_channel_abort_does_not_cancel_other_channels() -> None:
    acquisition, _, _ = controller()
    first = acquisition.initiate(1)
    second = acquisition.initiate(2)

    acquisition.abort(1)

    assert acquisition.channel(1).state is AcquisitionState.ABORTED
    assert first.state is OperationState.CANCELLED
    assert acquisition.channel(2).state is AcquisitionState.SWEEPING
    assert second.state is OperationState.PENDING


def test_auto_progress_completes_internal_sweep_and_opc_handshake() -> None:
    acquisition, operations, status = controller(auto_progress=True)
    status.set_event_status_enable(StandardEventBit.OPERATION_COMPLETE)
    status.set_service_request_enable(StatusByteBit.EVENT_STATUS)
    acquisition.set_sweep_time(1, 0.01)
    acquisition.initiate(1)
    operations.opc()

    deadline = monotonic() + 1
    while acquisition.channel(1).state is not AcquisitionState.COMPLETE:
        assert monotonic() < deadline
        sleep(0.005)

    assert status.event_status & StandardEventBit.OPERATION_COMPLETE
    assert status.status_byte == int(
        StatusByteBit.EVENT_STATUS | StatusByteBit.MASTER_STATUS_SUMMARY
    )


def test_registry_exposes_channel_defaults_trigger_modes_timing_and_averaging() -> None:
    acquisition, operations, status = controller()
    registry = CommandRegistry()
    register_status_commands(registry, status)
    register_operation_commands(registry, operations)
    register_acquisition_commands(registry, acquisition)

    assert dispatch(registry, "TRIG:SOUR BUS") == ""
    assert dispatch(registry, "TRIG:SOUR?") == "BUS"
    assert dispatch(registry, "TRIG:DEL 0.25S") == ""
    assert dispatch(registry, "TRIG:DEL?") == "0.25"
    assert dispatch(registry, "SENS2:SWE:TIME 0.5S") == ""
    assert dispatch(registry, "SENS2:SWE:TIME?") == "0.5"
    assert dispatch(registry, "SENS2:SWE:MODE GROUPS") == ""
    assert dispatch(registry, "SENS2:SWE:MODE?") == "GRO"
    assert dispatch(registry, "SENS2:SWE:GRO:COUN 3") == ""
    assert dispatch(registry, "SENS2:SWE:GRO:COUN?") == "3"
    assert dispatch(registry, "SENS2:AVER ON") == ""
    assert dispatch(registry, "SENS2:AVER?") == "1"
    assert dispatch(registry, "SENS2:AVER:COUN 4") == ""
    assert dispatch(registry, "SENS2:AVER:COUN?") == "4"
    assert dispatch(registry, "INIT2:CONT ON") == ""
    assert dispatch(registry, "INIT2:CONT?") == "1"

    assert dispatch(registry, "INIT2") == ""
    assert acquisition.channel(2).state is AcquisitionState.WAITING
    assert dispatch(registry, "*TRG") == ""
    assert acquisition.channel(2).state is AcquisitionState.WAITING
    assert acquisition.channel(2).trigger_received is True
    acquisition.delay_elapsed(2)
    assert acquisition.channel(2).state is AcquisitionState.SWEEPING
