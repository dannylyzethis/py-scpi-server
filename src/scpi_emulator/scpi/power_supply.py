"""Stateful generic triple-output power-supply subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .registry import (
    CommandRegistry,
    CommandSpec,
    HeaderNode,
    ParameterSpec,
    ParameterType,
    SCPICommandError,
)


@dataclass
class PowerSupplyOutput:
    """Independent configuration and protection state for one output."""

    voltage: Decimal = Decimal("0")
    current: Decimal = Decimal("0")
    enabled: bool = False
    voltage_protection: Decimal = Decimal("30")
    current_protection: Decimal = Decimal("5")
    voltage_range: str = "HIGH"
    current_range: str = "HIGH"
    protection_tripped: bool = False


class TripleOutputPowerSupply:
    """Own three independent outputs addressed through a selected-output context."""

    def __init__(self) -> None:
        self.selected_output = 1
        self.outputs: dict[int, PowerSupplyOutput] = {}
        self.reset()

    @property
    def selected(self) -> PowerSupplyOutput:
        return self.outputs[self.selected_output]

    def reset(self) -> None:
        self.selected_output = 1
        self.outputs = {number: PowerSupplyOutput() for number in range(1, 4)}

    def select(self, output: int) -> str:
        if output not in self.outputs:
            raise SCPICommandError(-222, "Data out of range; output must be 1, 2, or 3")
        self.selected_output = output
        return ""

    def select_name(self, name: str) -> str:
        return self.select(int(name[-1]))

    def set_voltage(self, value) -> str:
        self.selected.voltage = value.value
        return ""

    def set_current(self, value) -> str:
        self.selected.current = value.value
        return ""

    def set_output(self, enabled: bool) -> str:
        self.selected.enabled = enabled
        return ""

    def set_voltage_protection(self, value) -> str:
        self.selected.voltage_protection = value.value
        return ""

    def set_current_protection(self, value) -> str:
        self.selected.current_protection = value.value
        return ""

    def set_voltage_range(self, value: str) -> str:
        self.selected.voltage_range = value
        return ""

    def set_current_range(self, value: str) -> str:
        self.selected.current_range = value
        return ""

    def clear_protection(self) -> str:
        self.selected.protection_tripped = False
        return ""

    def measure_voltage(self) -> Decimal:
        return self.selected.voltage if self.selected.enabled else Decimal("0")

    def measure_current(self) -> Decimal:
        return self.selected.current if self.selected.enabled else Decimal("0")

    def inspect(self) -> dict[str, object]:
        return {
            "selected_output": self.selected_output,
            "outputs": {
                number: {
                    "voltage": str(output.voltage),
                    "current": str(output.current),
                    "enabled": output.enabled,
                    "voltage_protection": str(output.voltage_protection),
                    "current_protection": str(output.current_protection),
                    "voltage_range": output.voltage_range,
                    "current_range": output.current_range,
                    "protection_tripped": output.protection_tripped,
                }
                for number, output in self.outputs.items()
            },
        }


def register_power_supply_commands(
    registry: CommandRegistry,
    state: TripleOutputPowerSupply,
) -> None:
    """Register generic selected-output commands over independent channel state."""

    def number(name: str, maximum: str) -> ParameterSpec:
        return ParameterSpec(
            ParameterType.NUMBER,
            name=name,
            minimum=Decimal("0"),
            maximum=Decimal(maximum),
        )
    integer_output = ParameterSpec(
        ParameterType.INTEGER,
        name="output",
        minimum=1,
        maximum=3,
    )
    output_name = ParameterSpec(
        ParameterType.ENUM,
        name="output",
        choices=("OUT1", "OUT2", "OUT3"),
    )
    range_name = ParameterSpec(
        ParameterType.ENUM,
        name="range",
        choices=("LOW", "HIGH"),
    )

    registry.register(CommandSpec(
        (HeaderNode("INSTrument"), HeaderNode("NSELect")),
        lambda inv, output: state.select(output),
        (integer_output,),
    ))
    registry.register(CommandSpec(
        (HeaderNode("INSTrument"), HeaderNode("NSELect")),
        lambda inv: str(state.selected_output),
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("INSTrument"), HeaderNode("SELect")),
        lambda inv, output: state.select_name(output),
        (output_name,),
    ))
    registry.register(CommandSpec(
        (HeaderNode("INSTrument"), HeaderNode("SELect")),
        lambda inv: f"OUT{state.selected_output}",
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("INSTrument"), HeaderNode("CATalog")),
        lambda inv: "OUT1,OUT2,OUT3",
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("SYSTem"), HeaderNode("CHANnel"), HeaderNode("COUNt")),
        lambda inv: "3",
        query=True,
    ))

    registry.register(CommandSpec(
        (HeaderNode("OUTPut"),),
        lambda inv, enabled: state.set_output(enabled),
        (ParameterSpec(ParameterType.BOOLEAN, name="output state"),),
    ))
    registry.register(CommandSpec(
        (HeaderNode("OUTPut"),),
        lambda inv: "1" if state.selected.enabled else "0",
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("OUTPut"), HeaderNode("PROTection"), HeaderNode("CLEar")),
        lambda inv: state.clear_protection(),
    ))

    registry.register(CommandSpec(
        (HeaderNode("VOLTage"),),
        lambda inv, value: state.set_voltage(value),
        (number("voltage", "30"),),
    ))
    registry.register(CommandSpec(
        (HeaderNode("VOLTage"),),
        lambda inv: _format(state.selected.voltage),
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"),),
        lambda inv, value: state.set_current(value),
        (number("current", "5"),),
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"),),
        lambda inv: _format(state.selected.current),
        query=True,
    ))

    registry.register(CommandSpec(
        (HeaderNode("VOLTage"), HeaderNode("PROTection")),
        lambda inv, value: state.set_voltage_protection(value),
        (number("voltage protection", "32"),),
    ))
    registry.register(CommandSpec(
        (HeaderNode("VOLTage"), HeaderNode("PROTection")),
        lambda inv: _format(state.selected.voltage_protection),
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"), HeaderNode("PROTection")),
        lambda inv, value: state.set_current_protection(value),
        (number("current protection", "5.2"),),
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"), HeaderNode("PROTection")),
        lambda inv: _format(state.selected.current_protection),
        query=True,
    ))

    registry.register(CommandSpec(
        (HeaderNode("VOLTage"), HeaderNode("RANGe")),
        lambda inv, value: state.set_voltage_range(value),
        (range_name,),
    ))
    registry.register(CommandSpec(
        (HeaderNode("VOLTage"), HeaderNode("RANGe")),
        lambda inv: state.selected.voltage_range,
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"), HeaderNode("RANGe")),
        lambda inv, value: state.set_current_range(value),
        (range_name,),
    ))
    registry.register(CommandSpec(
        (HeaderNode("CURRent"), HeaderNode("RANGe")),
        lambda inv: state.selected.current_range,
        query=True,
    ))

    registry.register(CommandSpec(
        (HeaderNode("MEASure"), HeaderNode("VOLTage")),
        lambda inv: _format(state.measure_voltage()),
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("MEASure"), HeaderNode("CURRent")),
        lambda inv: _format(state.measure_current()),
        query=True,
    ))
    registry.register(CommandSpec(
        (HeaderNode("MEASure"), HeaderNode("POWer")),
        lambda inv: _format(state.measure_voltage() * state.measure_current()),
        query=True,
    ))


def _format(value: Decimal) -> str:
    return f"{float(value):.6E}"
