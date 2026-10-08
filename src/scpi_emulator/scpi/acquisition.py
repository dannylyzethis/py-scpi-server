"""Channel-aware trigger and acquisition state machines."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, IntFlag
from threading import RLock, Timer
from typing import Protocol

from .operations import OperationHandle, OperationManager, OperationState
from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)
from .status import StatusSystem


class AcquisitionState(Enum):
    IDLE = "idle"
    ARMED = "armed"
    WAITING = "waiting"
    SWEEPING = "sweeping"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ABORTED = "aborted"


class TriggerSource(Enum):
    INTERNAL = "INT"
    MANUAL = "MAN"
    EXTERNAL = "EXT"
    BUS = "BUS"


class SweepMode(Enum):
    HOLD = "HOLD"
    SINGLE = "SING"
    GROUPS = "GRO"
    CONTINUOUS = "CONT"


class OperationConditionBit(IntFlag):
    SWEEPING = 1 << 3
    MEASURING = 1 << 4
    WAITING_FOR_TRIGGER = 1 << 5


class ScheduledCall(Protocol):
    def cancel(self) -> None: ...


class Scheduler(Protocol):
    def schedule(self, delay: float, callback) -> ScheduledCall: ...


class ThreadTimerScheduler:
    def schedule(self, delay: float, callback) -> Timer:
        timer = Timer(delay, callback)
        timer.daemon = True
        timer.start()
        return timer


@dataclass
class AcquisitionChannel:
    number: int
    state: AcquisitionState = AcquisitionState.IDLE
    trigger_source: TriggerSource = TriggerSource.INTERNAL
    trigger_delay: float = 0.0
    sweep_time: float = 0.01
    processing_time: float = 0.001
    continuous: bool = False
    sweep_mode: SweepMode = SweepMode.SINGLE
    group_count: int = 1
    averaging_enabled: bool = False
    averaging_count: int = 1
    averaging_mode: str = "SWEep"
    averages_completed: int = 0
    trigger_received: bool = False
    operation: OperationHandle | None = None
    generation: int = 0
    scheduled: list[ScheduledCall] = field(default_factory=list, repr=False)
    auxiliary: dict[int, TriggerAuxiliary] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.state in {
            AcquisitionState.ARMED,
            AcquisitionState.WAITING,
            AcquisitionState.SWEEPING,
            AcquisitionState.PROCESSING,
        }


@dataclass
class TriggerAuxiliary:
    enabled: bool = False
    delay: float = 0.0
    duration: float = 0.001
    handshake: bool = False
    interval: float = 0.0
    input_polarity: str = "POSitive"
    output_polarity: str = "POSitive"
    position: str = "BEFore"
    trigger_type: str = "EDGE"


class AcquisitionController:
    """Coordinates independent channel acquisitions and shared trigger inputs."""

    def __init__(
        self,
        operations: OperationManager,
        status: StatusSystem,
        *,
        auto_progress: bool = True,
        scheduler: Scheduler | None = None,
    ) -> None:
        self.operations = operations
        self.status = status
        self.auto_progress = auto_progress
        self.scheduler = scheduler if scheduler is not None else ThreadTimerScheduler()
        self._channels: dict[int, AcquisitionChannel] = {}
        self._default_trigger_source = TriggerSource.INTERNAL
        self._default_trigger_delay = 0.0
        self.trigger_scope = "ALL"
        self.trigger_slope = "POSitive"
        self.trigger_type = "EDGE"
        self.trigger_route_input = "MAIN"
        self.trigger_route_ready = "MAIN"
        self.trigger_ready_polarity = "POSitive"
        self.trigger_preference_ai_global = False
        self._lock = RLock()
        self._trigger_listeners = []
        self._completion_listeners = []
        operations.add_abort_listener(self._on_global_abort)

    def add_trigger_listener(self, listener) -> None:
        self._trigger_listeners.append(listener)

    def add_completion_listener(self, listener) -> None:
        self._completion_listeners.append(listener)

    def remove_trigger_listener(self, listener) -> None:
        if listener in self._trigger_listeners:
            self._trigger_listeners.remove(listener)

    def remove_completion_listener(self, listener) -> None:
        if listener in self._completion_listeners:
            self._completion_listeners.remove(listener)

    def channel(self, number: int = 1) -> AcquisitionChannel:
        number = _channel_number(number)
        with self._lock:
            if number not in self._channels:
                self._channels[number] = AcquisitionChannel(
                    number,
                    trigger_source=self._default_trigger_source,
                    trigger_delay=self._default_trigger_delay,
                )
            return self._channels[number]

    def inspect(self) -> dict[str, object]:
        """Return trigger and acquisition state without creating channels."""
        with self._lock:
            channels = [
                {
                    "number": channel.number,
                    "state": channel.state.value,
                    "trigger_source": channel.trigger_source.value,
                    "trigger_delay": channel.trigger_delay,
                    "sweep_time": channel.sweep_time,
                    "continuous": channel.continuous,
                    "sweep_mode": channel.sweep_mode.value,
                    "group_count": channel.group_count,
                    "averaging_enabled": channel.averaging_enabled,
                    "averaging_count": channel.averaging_count,
                    "averages_completed": channel.averages_completed,
                    "trigger_received": channel.trigger_received,
                }
                for channel in sorted(self._channels.values(), key=lambda item: item.number)
            ]
            return {
                "default_trigger_source": self._default_trigger_source.value,
                "default_trigger_delay": self._default_trigger_delay,
                "channels": channels,
            }

    def initiate(self, number: int = 1) -> OperationHandle:
        with self._lock:
            channel = self.channel(number)
            if channel.active:
                self._abort_channel_locked(channel)
            channel.generation += 1
            channel.averages_completed = 0
            channel.trigger_received = False
            channel.operation = self.operations.begin(f"channel {number} acquisition")
            channel.state = AcquisitionState.ARMED
            self._update_operation_condition()
            if channel.trigger_source is TriggerSource.INTERNAL:
                self._accept_trigger_locked(channel)
            else:
                channel.state = AcquisitionState.WAITING
                self._update_operation_condition()
            return channel.operation

    def set_trigger_source(self, source: TriggerSource | str, number: int | None = None) -> None:
        source = source if isinstance(source, TriggerSource) else TriggerSource(source.upper())
        with self._lock:
            if number is None:
                self._default_trigger_source = source
                for channel in self._channels.values():
                    channel.trigger_source = source
            else:
                self.channel(number).trigger_source = source

    def trigger_source(self, number: int | None = None) -> TriggerSource:
        with self._lock:
            return (
                self._default_trigger_source
                if number is None
                else self.channel(number).trigger_source
            )

    def set_trigger_delay(self, delay: float, number: int | None = None) -> None:
        if not 0 <= delay <= 3600:
            raise ValueError("trigger delay must be between 0 and 3600 seconds")
        with self._lock:
            if number is None:
                self._default_trigger_delay = delay
                for channel in self._channels.values():
                    channel.trigger_delay = delay
            else:
                self.channel(number).trigger_delay = delay

    def trigger_delay(self, number: int | None = None) -> float:
        with self._lock:
            return (
                self._default_trigger_delay
                if number is None
                else self.channel(number).trigger_delay
            )

    def auxiliary(self, channel: int, index: int) -> TriggerAuxiliary:
        if not 1 <= index <= 2:
            raise SCPICommandError(-222, "Data out of range; auxiliary trigger index")
        return self.channel(channel).auxiliary.setdefault(index, TriggerAuxiliary())

    def trigger_ready(self) -> bool:
        return all(
            channel.state not in {AcquisitionState.SWEEPING, AcquisitionState.PROCESSING}
            for channel in self._channels.values()
        )

    def set_sweep_time(self, number: int, duration: float) -> None:
        if duration < 0:
            raise ValueError("sweep time cannot be negative")
        with self._lock:
            self.channel(number).sweep_time = duration

    def set_processing_time(self, number: int, duration: float) -> None:
        if duration < 0:
            raise ValueError("processing time cannot be negative")
        with self._lock:
            self.channel(number).processing_time = duration

    def set_continuous(self, number: int, enabled: bool) -> None:
        with self._lock:
            channel = self.channel(number)
            channel.continuous = enabled
            channel.sweep_mode = SweepMode.CONTINUOUS if enabled else SweepMode.SINGLE

    def set_sweep_mode(self, number: int, mode: SweepMode | str) -> None:
        aliases = {
            "HOLD": SweepMode.HOLD,
            "SING": SweepMode.SINGLE,
            "SINGLE": SweepMode.SINGLE,
            "GRO": SweepMode.GROUPS,
            "GROUPS": SweepMode.GROUPS,
            "CONT": SweepMode.CONTINUOUS,
            "CONTINUOUS": SweepMode.CONTINUOUS,
        }
        if not isinstance(mode, SweepMode):
            try:
                mode = aliases[mode.upper()]
            except KeyError as error:
                raise ValueError(f"invalid sweep mode {mode!r}") from error
        with self._lock:
            channel = self.channel(number)
            channel.sweep_mode = mode
            channel.continuous = mode is SweepMode.CONTINUOUS

    def set_group_count(self, number: int, count: int) -> None:
        if not 1 <= count <= 65536:
            raise ValueError("group count must be between 1 and 65536")
        with self._lock:
            self.channel(number).group_count = count

    def set_averaging(self, number: int, enabled: bool) -> None:
        with self._lock:
            self.channel(number).averaging_enabled = enabled

    def set_averaging_count(self, number: int, count: int) -> None:
        if not 1 <= count <= 65536:
            raise ValueError("averaging count must be between 1 and 65536")
        with self._lock:
            self.channel(number).averaging_count = count

    def manual_trigger(self, number: int | None = None) -> int:
        return self.trigger(TriggerSource.MANUAL, number)

    def external_trigger(self, number: int | None = None) -> int:
        return self.trigger(TriggerSource.EXTERNAL, number)

    def bus_trigger(self, number: int | None = None) -> int:
        return self.trigger(TriggerSource.BUS, number)

    def trigger(self, source: TriggerSource, number: int | None = None) -> int:
        with self._lock:
            channels = (
                [self.channel(number)] if number is not None else list(self._channels.values())
            )
            accepted = 0
            for channel in channels:
                if (
                    channel.state is AcquisitionState.WAITING
                    and not channel.trigger_received
                    and channel.trigger_source is source
                ):
                    self._accept_trigger_locked(channel)
                    accepted += 1
            return accepted

    def delay_elapsed(self, number: int) -> None:
        with self._lock:
            channel = self.channel(number)
            if channel.state is AcquisitionState.WAITING and channel.trigger_received:
                self._start_sweep_locked(channel)

    def complete_sweep(self, number: int) -> None:
        with self._lock:
            channel = self.channel(number)
            if channel.state is not AcquisitionState.SWEEPING:
                raise RuntimeError(f"channel {number} is not sweeping")
            channel.state = AcquisitionState.PROCESSING
            self._update_operation_condition()
            if self.auto_progress:
                self._schedule_locked(channel, channel.processing_time, self.complete_processing)

    def complete_processing(self, number: int) -> None:
        with self._lock:
            channel = self.channel(number)
            if channel.state is not AcquisitionState.PROCESSING:
                raise RuntimeError(f"channel {number} is not processing")
            channel.averages_completed += 1
            if channel.averaging_enabled:
                target = channel.averaging_count
            elif channel.sweep_mode is SweepMode.GROUPS:
                target = channel.group_count
            else:
                target = 1
            if channel.averages_completed < target:
                channel.state = AcquisitionState.WAITING
                channel.trigger_received = False
                self._update_operation_condition()
                if channel.trigger_source is TriggerSource.INTERNAL:
                    self._accept_trigger_locked(channel)
                return

            channel.state = AcquisitionState.COMPLETE
            operation = channel.operation
            self._update_operation_condition()
            if operation is not None and operation.state is OperationState.PENDING:
                operation.complete()
            for listener in tuple(self._completion_listeners):
                listener(number)
            if channel.sweep_mode is SweepMode.CONTINUOUS and self.auto_progress:
                generation = channel.generation
                self._schedule_locked(
                    channel,
                    0,
                    lambda selected: self._restart_continuous(selected, generation),
                )

    def abort(self, number: int | None = None) -> None:
        if number is None:
            self.operations.abort()
            return
        with self._lock:
            self._abort_channel_locked(self.channel(number))
            self._update_operation_condition()

    def _accept_trigger_locked(self, channel: AcquisitionChannel) -> None:
        channel.trigger_received = True
        for listener in tuple(self._trigger_listeners):
            listener(channel.number)
        channel.state = AcquisitionState.WAITING
        self._update_operation_condition()
        if channel.trigger_delay == 0:
            self._start_sweep_locked(channel)
        elif self.auto_progress:
            self._schedule_locked(channel, channel.trigger_delay, self.delay_elapsed)

    def _start_sweep_locked(self, channel: AcquisitionChannel) -> None:
        channel.state = AcquisitionState.SWEEPING
        self._update_operation_condition()
        if self.auto_progress:
            self._schedule_locked(channel, channel.sweep_time, self.complete_sweep)

    def _abort_channel_locked(self, channel: AcquisitionChannel) -> None:
        channel.generation += 1
        for scheduled in channel.scheduled:
            scheduled.cancel()
        channel.scheduled.clear()
        operation = channel.operation
        if operation is not None and operation.state is OperationState.PENDING:
            operation.cancel()
        if channel.active or operation is not None:
            channel.state = AcquisitionState.ABORTED

    def _on_global_abort(self) -> None:
        with self._lock:
            for channel in self._channels.values():
                if channel.active:
                    channel.generation += 1
                    for scheduled in channel.scheduled:
                        scheduled.cancel()
                    channel.scheduled.clear()
                    channel.state = AcquisitionState.ABORTED
            self._update_operation_condition()

    def _restart_continuous(self, number: int, generation: int) -> None:
        with self._lock:
            channel = self.channel(number)
            if channel.generation != generation or channel.sweep_mode is not SweepMode.CONTINUOUS:
                return
        self.initiate(number)

    def _schedule_locked(self, channel: AcquisitionChannel, delay: float, callback) -> None:
        generation = channel.generation

        def guarded() -> None:
            with self._lock:
                current = self.channel(channel.number)
                if current.generation != generation or current.state is AcquisitionState.ABORTED:
                    return
            callback(channel.number)

        channel.scheduled.append(self.scheduler.schedule(delay, guarded))

    def _update_operation_condition(self) -> None:
        condition = 0
        for channel in self._channels.values():
            if channel.state in (AcquisitionState.ARMED, AcquisitionState.WAITING):
                condition |= OperationConditionBit.WAITING_FOR_TRIGGER
            elif channel.state is AcquisitionState.SWEEPING:
                condition |= OperationConditionBit.SWEEPING | OperationConditionBit.MEASURING
            elif channel.state is AcquisitionState.PROCESSING:
                condition |= OperationConditionBit.MEASURING
        self.status.operation.set_condition(int(condition))


def register_acquisition_commands(
    registry: CommandRegistry,
    acquisition: AcquisitionController,
    *,
    include_sweep_time: bool = True,
) -> None:
    """Register common VNA acquisition, trigger, timing, and averaging commands."""
    channel_node = HeaderNode("INITiate", index="channel", index_default=1)
    sense_node = HeaderNode("SENSe", index="channel", index_default=1)
    for path in ((channel_node,), (channel_node, HeaderNode("IMMediate"))):
        registry.register(
            CommandSpec(
                path=path,
                handler=lambda invocation: _initiate_response(
                    acquisition, invocation.indices["channel"]
                ),
            )
        )
    _register_channel_boolean(
        registry,
        (channel_node, HeaderNode("CONTinuous")),
        lambda channel, value: acquisition.set_continuous(channel, value),
        lambda channel: acquisition.channel(channel).continuous,
    )
    _register_channel_boolean(
        registry,
        (sense_node, HeaderNode("AVERage")),
        lambda channel, value: acquisition.set_averaging(channel, value),
        lambda channel: acquisition.channel(channel).averaging_enabled,
    )
    _register_channel_boolean(
        registry,
        (sense_node, HeaderNode("AVERage"), HeaderNode("STATe")),
        lambda channel, value: acquisition.set_averaging(channel, value),
        lambda channel: acquisition.channel(channel).averaging_enabled,
    )
    _register_channel_integer(
        registry,
        (sense_node, HeaderNode("AVERage"), HeaderNode("COUNt")),
        lambda channel, value: acquisition.set_averaging_count(channel, value),
        lambda channel: acquisition.channel(channel).averaging_count,
        1,
        65536,
    )
    average_mode_path = (sense_node, HeaderNode("AVERage"), HeaderNode("MODE"))
    registry.register(
        CommandSpec(
            path=average_mode_path,
            parameters=(
                ParameterSpec(
                    ParameterType.ENUM,
                    choices=("SWEep", "POINt"),
                ),
            ),
            handler=lambda invocation, value: _empty(
                setattr(acquisition.channel(invocation.indices["channel"]), "averaging_mode", value)
            ),
        )
    )
    registry.register(
        CommandSpec(
            path=average_mode_path,
            handler=lambda invocation: (
                acquisition.channel(invocation.indices["channel"]).averaging_mode
            ),
            query=True,
        )
    )
    registry.register(
        CommandSpec(
            path=(sense_node, HeaderNode("AVERage"), HeaderNode("CLEar")),
            handler=lambda invocation: _clear_average(
                acquisition.channel(invocation.indices["channel"])
            ),
        )
    )
    if include_sweep_time:
        _register_channel_number(
            registry,
            (sense_node, HeaderNode("SWEep"), HeaderNode("TIME")),
            lambda channel, value: acquisition.set_sweep_time(channel, float(value.value)),
            lambda channel: acquisition.channel(channel).sweep_time,
            0,
            1_000_000,
        )
    sweep_mode_path = (sense_node, HeaderNode("SWEep"), HeaderNode("MODE"))
    registry.register(
        CommandSpec(
            path=sweep_mode_path,
            parameters=(
                ParameterSpec(
                    ParameterType.ENUM,
                    "sweep mode",
                    choices=("HOLD", "SING", "SINGLE", "GRO", "GROUPS", "CONT", "CONTINUOUS"),
                ),
            ),
            handler=lambda invocation, value: _empty(
                acquisition.set_sweep_mode(invocation.indices["channel"], value)
            ),
        )
    )
    registry.register(
        CommandSpec(
            path=sweep_mode_path,
            handler=lambda invocation: (
                acquisition.channel(invocation.indices["channel"]).sweep_mode.value
            ),
            query=True,
        )
    )
    _register_channel_integer(
        registry,
        (sense_node, HeaderNode("SWEep"), HeaderNode("GROups"), HeaderNode("COUNt")),
        lambda channel, value: acquisition.set_group_count(channel, value),
        lambda channel: acquisition.channel(channel).group_count,
        1,
        65536,
    )
    _register_trigger_commands(registry, acquisition)
    for path, mode in (
        ((HeaderNode("SYSTem"), HeaderNode("CHANnels"), HeaderNode("HOLD")), SweepMode.HOLD),
        (
            (HeaderNode("SYSTem"), HeaderNode("CHANnels"), HeaderNode("RESume")),
            SweepMode.CONTINUOUS,
        ),
    ):
        registry.register(
            CommandSpec(
                path=path,
                handler=lambda invocation, value=mode: _set_all_sweep_modes(acquisition, value),
            )
        )
    for path in (
        (HeaderNode("SYSTem"), HeaderNode("CHANnels"), HeaderNode("SINGle")),
        (
            HeaderNode("SYSTem"),
            HeaderNode("CHANnels"),
            HeaderNode("SINGle"),
            HeaderNode("COMBine"),
        ),
    ):
        registry.register(
            CommandSpec(path=path, handler=lambda invocation: _single_all(acquisition))
        )


def _register_trigger_commands(
    registry: CommandRegistry, acquisition: AcquisitionController
) -> None:
    def pair(path, reader, setter, parameter):
        registry.register(
            CommandSpec(
                path=path,
                parameters=(parameter,),
                handler=lambda invocation, value: _empty(setter(invocation, value)),
            )
        )
        registry.register(
            CommandSpec(path=path, handler=lambda invocation: reader(invocation), query=True)
        )

    source_parameters = (
        ParameterSpec(ParameterType.ENUM, "trigger source", choices=("INT", "MAN", "EXT", "BUS")),
    )
    for path in (
        (HeaderNode("TRIGger"), HeaderNode("SOURce")),
        (HeaderNode("TRIGger"), HeaderNode("SEQuence"), HeaderNode("SOURce")),
    ):
        registry.register(
            CommandSpec(
                path=path,
                parameters=source_parameters,
                handler=lambda invocation, value: _empty(acquisition.set_trigger_source(value)),
            )
        )
        registry.register(
            CommandSpec(
                path=path,
                handler=lambda invocation: acquisition.trigger_source().value,
                query=True,
            )
        )
    delay_path = (HeaderNode("TRIGger"), HeaderNode("DELay"))
    registry.register(
        CommandSpec(
            path=delay_path,
            parameters=(
                ParameterSpec(
                    ParameterType.NUMBER,
                    "trigger delay",
                    minimum=Decimal(0),
                    maximum=Decimal(3600),
                    units=frozenset({"S"}),
                ),
            ),
            handler=lambda invocation, value: _empty(
                acquisition.set_trigger_delay(float(value.value))
            ),
        )
    )
    registry.register(
        CommandSpec(
            path=delay_path,
            handler=lambda invocation: str(acquisition.trigger_delay()),
            query=True,
        )
    )
    boolean = ParameterSpec(ParameterType.BOOLEAN)
    second = ParameterSpec(
        ParameterType.NUMBER,
        minimum=Decimal(0),
        maximum=Decimal(3600),
        units=frozenset({"S"}),
    )
    trigger = HeaderNode("TRIGger")
    sequence = HeaderNode("SEQuence")

    pair(
        (trigger, HeaderNode("PREFerence"), HeaderNode("AIGLobal")),
        lambda inv: _bool(acquisition.trigger_preference_ai_global),
        lambda inv, value: setattr(acquisition, "trigger_preference_ai_global", value),
        boolean,
    )
    pair(
        (trigger, HeaderNode("READy"), HeaderNode("POLarity")),
        lambda inv: acquisition.trigger_ready_polarity,
        lambda inv, value: setattr(acquisition, "trigger_ready_polarity", value),
        ParameterSpec(ParameterType.ENUM, choices=("POSitive", "NEGative")),
    )
    registry.register(
        CommandSpec(
            path=(trigger, HeaderNode("STATus"), HeaderNode("READy")),
            handler=lambda inv: _bool(acquisition.trigger_ready()),
            query=True,
        )
    )
    for prefix in ((trigger,), (trigger, sequence)):
        for suffix, attribute, choices in (
            ((HeaderNode("SCOPe"),), "trigger_scope", ("ALL", "ACTive", "CHANnel")),
            ((HeaderNode("SLOPe"),), "trigger_slope", ("POSitive", "NEGative")),
            ((HeaderNode("TYPE"),), "trigger_type", ("EDGE", "LEVel")),
            (
                (HeaderNode("ROUTe"), HeaderNode("INPut")),
                "trigger_route_input",
                ("MAIN", "AUXiliary"),
            ),
            (
                (HeaderNode("ROUTe"), HeaderNode("READy")),
                "trigger_route_ready",
                ("MAIN", "AUXiliary"),
            ),
        ):
            pair(
                (*prefix, *suffix),
                lambda inv, attr=attribute: getattr(acquisition, attr),
                lambda inv, value, attr=attribute: setattr(acquisition, attr, value),
                ParameterSpec(ParameterType.ENUM, choices=choices),
            )

    registry.register(
        CommandSpec(
            path=(trigger, HeaderNode("AUXiliary"), HeaderNode("COUNt")),
            handler=lambda inv: "2",
            query=True,
        )
    )
    trigger_channel = HeaderNode("CHANnel", index="channel", index_default=1)
    auxiliary = HeaderNode("AUXiliary", index="auxiliary", index_default=1)

    def auxiliary_state(invocation) -> TriggerAuxiliary:
        return acquisition.auxiliary(invocation.indices["channel"], invocation.indices["auxiliary"])

    for path in (
        (trigger, trigger_channel, auxiliary),
        (trigger, trigger_channel, auxiliary, HeaderNode("ENABle")),
    ):
        pair(
            path,
            lambda inv: _bool(auxiliary_state(inv).enabled),
            lambda inv, value: setattr(auxiliary_state(inv), "enabled", value),
            boolean,
        )
    for header, attribute in (
        ("DELay", "delay"),
        ("DURation", "duration"),
        ("INTerval", "interval"),
    ):
        pair(
            (trigger, trigger_channel, auxiliary, HeaderNode(header)),
            lambda inv, attr=attribute: str(getattr(auxiliary_state(inv), attr)),
            lambda inv, value, attr=attribute: setattr(
                auxiliary_state(inv), attr, float(value.value)
            ),
            second,
        )
    pair(
        (trigger, trigger_channel, auxiliary, HeaderNode("HANDshake")),
        lambda inv: _bool(auxiliary_state(inv).handshake),
        lambda inv, value: setattr(auxiliary_state(inv), "handshake", value),
        boolean,
    )
    for header, attribute, choices in (
        ("IPOLarity", "input_polarity", ("POSitive", "NEGative")),
        ("OPOLarity", "output_polarity", ("POSitive", "NEGative")),
        ("POSition", "position", ("BEFore", "AFTer")),
        ("TYPE", "trigger_type", ("EDGE", "LEVel")),
    ):
        pair(
            (trigger, trigger_channel, auxiliary, HeaderNode(header)),
            lambda inv, attr=attribute: getattr(auxiliary_state(inv), attr),
            lambda inv, value, attr=attribute: setattr(auxiliary_state(inv), attr, value),
            ParameterSpec(ParameterType.ENUM, choices=choices),
        )
    registry.register(
        CommandSpec(
            path=(HeaderNode("TRIGger"), HeaderNode("IMMediate")),
            handler=lambda invocation: _trigger_response(acquisition.manual_trigger()),
        )
    )
    registry.register(
        CommandSpec(
            path=(HeaderNode("*TRG"),),
            handler=lambda invocation: _trigger_response(acquisition.bus_trigger()),
            common=True,
        )
    )


def _register_channel_boolean(registry, path, setter, reader) -> None:
    registry.register(
        CommandSpec(
            path=path,
            parameters=(ParameterSpec(ParameterType.BOOLEAN),),
            handler=lambda invocation, value: _empty(setter(invocation.indices["channel"], value)),
        )
    )
    registry.register(
        CommandSpec(
            path=path,
            handler=lambda invocation: "1" if reader(invocation.indices["channel"]) else "0",
            query=True,
        )
    )


def _register_channel_integer(registry, path, setter, reader, minimum, maximum) -> None:
    registry.register(
        CommandSpec(
            path=path,
            parameters=(ParameterSpec(ParameterType.INTEGER, minimum=minimum, maximum=maximum),),
            handler=lambda invocation, value: _empty(setter(invocation.indices["channel"], value)),
        )
    )
    registry.register(
        CommandSpec(
            path=path,
            handler=lambda invocation: str(reader(invocation.indices["channel"])),
            query=True,
        )
    )


def _register_channel_number(registry, path, setter, reader, minimum, maximum) -> None:
    registry.register(
        CommandSpec(
            path=path,
            parameters=(
                ParameterSpec(
                    ParameterType.NUMBER, minimum=minimum, maximum=maximum, units=frozenset({"S"})
                ),
            ),
            handler=lambda invocation, value: _empty(setter(invocation.indices["channel"], value)),
        )
    )
    registry.register(
        CommandSpec(
            path=path,
            handler=lambda invocation: str(reader(invocation.indices["channel"])),
            query=True,
        )
    )


def _initiate_response(acquisition: AcquisitionController, channel: int) -> str:
    acquisition.initiate(channel)
    return ""


def _trigger_response(accepted: int) -> str:
    return ""


def _clear_average(channel: AcquisitionChannel) -> str:
    channel.averages_completed = 0
    return ""


def _set_all_sweep_modes(acquisition: AcquisitionController, mode: SweepMode) -> str:
    channels = tuple(acquisition._channels) or (1,)
    for number in channels:
        acquisition.set_sweep_mode(number, mode)
    return ""


def _single_all(acquisition: AcquisitionController) -> str:
    channels = tuple(acquisition._channels) or (1,)
    for number in channels:
        acquisition.set_sweep_mode(number, SweepMode.SINGLE)
        acquisition.initiate(number)
    return ""


def _empty(value) -> str:
    return ""


def _bool(value: bool) -> str:
    return "1" if value else "0"


def _channel_number(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("channel number must be a positive integer")
    return value
