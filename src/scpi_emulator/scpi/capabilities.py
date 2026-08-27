"""Project-owned VNA hardware, application, and identity capabilities."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from .registry import CommandRegistry, CommandSpec, HeaderNode, ParameterSpec, ParameterType

DEFAULT_FREQUENCY_MINIMUM_HZ = 10_000_000
DEFAULT_FREQUENCY_MAXIMUM_HZ = 50_000_000_000
GENERIC_VNA_MODELS = ("vna-2-port", "vna-4-port")
ALL_HARDWARE_FEATURES = (
    "bias_tees",
    "direct_receiver_access",
    "internal_combiner",
    "internal_rf_switches",
    "noise_receiver",
    "pulse_control",
    "receiver_attenuators",
    "source_attenuators",
)


class CapabilityError(ValueError):
    """Raised when a requested VNA configuration cannot exist."""


@dataclass(frozen=True)
class VNACapabilities:
    model: str
    instrument_class: str
    ports: int
    source_count: int
    hardware_features: frozenset[str]
    applications: tuple[str, ...]
    frequency_minimum: int | float
    frequency_maximum: int | float
    serial: str
    firmware: str

    @classmethod
    def create(
        cls,
        model: str,
        *,
        source_count: int | None = None,
        hardware_features: tuple[str, ...] | list[str] | None = None,
        applications: tuple[str, ...] | list[str] | None = None,
        frequency_minimum_hz: int | float | None = None,
        frequency_maximum_hz: int | float | None = None,
        serial: str = "EMU00000001",
        firmware: str = "E.1.0",
    ) -> "VNACapabilities":
        profile = _load_capability_profile()
        model = model.casefold()
        models = profile["models"]
        if model not in models:
            valid = ", ".join(sorted(models))
            raise CapabilityError(f"unsupported VNA model {model!r}; choose {valid}")
        model_data = models[model]
        ports = model_data["ports"]
        selected_sources = (
            model_data["default_source_count"] if source_count is None else source_count
        )
        if isinstance(selected_sources, bool) or not isinstance(selected_sources, int):
            raise CapabilityError("source_count must be an integer")
        if selected_sources not in (1, 2):
            raise CapabilityError("source_count must be 1 or 2")

        selected_hardware = _select_hardware_features(hardware_features, profile)
        selected_applications = _select_applications(
            applications,
            profile["applications"],
            ports=ports,
            source_count=selected_sources,
            hardware_features=selected_hardware,
        )
        frequency_minimum, frequency_maximum = _resolve_frequency_range(
            frequency_minimum_hz,
            frequency_maximum_hz,
        )
        return cls(
            model=model,
            instrument_class="VNA",
            ports=ports,
            source_count=selected_sources,
            hardware_features=selected_hardware,
            applications=selected_applications,
            frequency_minimum=frequency_minimum,
            frequency_maximum=frequency_maximum,
            serial=serial,
            firmware=firmware,
        )

    @property
    def identification(self) -> str:
        return f"SCPI Emulator,{self.model},{self.serial},{self.firmware}"

    @property
    def port_names(self) -> tuple[str, ...]:
        return tuple(f"Port {port}" for port in range(1, self.ports + 1))

    @property
    def receiver_count(self) -> int:
        return self.ports + self.source_count

    @property
    def has_attenuators(self) -> bool:
        return {"source_attenuators", "receiver_attenuators"} <= self.hardware_features

    @property
    def has_direct_receiver_access(self) -> bool:
        return "direct_receiver_access" in self.hardware_features

    @property
    def has_low_frequency_extension(self) -> bool:
        return self.frequency_minimum < DEFAULT_FREQUENCY_MINIMUM_HZ

    @property
    def option_query_tokens(self) -> tuple[str, ...]:
        hardware = tuple(f"HW-{_token(item)}" for item in sorted(self.hardware_features))
        applications = tuple(f"APP-{_token(item)}" for item in self.applications)
        return (f"PORTS-{self.ports}", f"SOURCES-{self.source_count}", *hardware, *applications)

    def license_catalog(self, selection: str) -> tuple[str, ...]:
        if selection.upper() == "IGNORED":
            return ()
        return tuple(f"APP-{_token(item)}" for item in self.applications)

    @property
    def license_feature_names(self) -> tuple[str, ...]:
        return tuple(f"APP-{_token(item)}" for item in self.applications)

    def feature_enabled(self, name: str) -> bool:
        normalized = _normalize_identifier(name.removeprefix("APP-").removeprefix("app-"))
        return normalized in self.applications

    @property
    def command_capabilities(self) -> frozenset[str]:
        names: set[str] = set()
        for application in self.applications:
            names.update(
                {
                    application,
                    application.replace("_", "-"),
                    application.replace("_", " ").title().casefold(),
                }
            )
        return frozenset(names)


def _select_hardware_features(
    requested: tuple[str, ...] | list[str] | None,
    profile: dict[str, Any],
) -> frozenset[str]:
    available = frozenset(profile["hardware_features"])
    if requested is None:
        return available
    normalized = tuple(_normalize_identifier(value) for value in requested)
    if "all" in normalized:
        if len(normalized) != 1:
            raise CapabilityError("hardware_features 'all' cannot be combined with other values")
        return available
    unknown = set(normalized) - available
    if unknown:
        raise CapabilityError(f"unknown VNA hardware features: {sorted(unknown)}")
    return frozenset(normalized)


def _select_applications(
    requested: tuple[str, ...] | list[str] | None,
    available: dict[str, Any],
    *,
    ports: int,
    source_count: int,
    hardware_features: frozenset[str],
) -> tuple[str, ...]:
    normalized = (
        ("all",)
        if requested is None
        else tuple(dict.fromkeys(_normalize_identifier(value) for value in requested))
    )
    if "all" in normalized:
        if len(normalized) != 1:
            raise CapabilityError("applications 'all' cannot be combined with other values")
        return tuple(
            application
            for application, requirements in sorted(available.items())
            if _application_is_compatible(
                requirements,
                ports=ports,
                source_count=source_count,
                hardware_features=hardware_features,
            )
        )

    unknown = set(normalized) - set(available)
    if unknown:
        raise CapabilityError(f"unknown VNA applications: {sorted(unknown)}")
    expanded: set[str] = set()

    def add_with_dependencies(application: str) -> None:
        if application in expanded:
            return
        for dependency in available[application].get("requires_applications", ()):
            add_with_dependencies(dependency)
        expanded.add(application)

    for application in normalized:
        add_with_dependencies(application)
    for application in sorted(expanded):
        _validate_application_requirements(
            application,
            available[application],
            ports=ports,
            source_count=source_count,
            hardware_features=hardware_features,
        )
    return tuple(sorted(expanded))


def _application_is_compatible(
    requirements: dict[str, Any],
    *,
    ports: int,
    source_count: int,
    hardware_features: frozenset[str],
) -> bool:
    try:
        _validate_application_requirements(
            "candidate",
            requirements,
            ports=ports,
            source_count=source_count,
            hardware_features=hardware_features,
        )
    except CapabilityError:
        return False
    return True


def _validate_application_requirements(
    application: str,
    requirements: dict[str, Any],
    *,
    ports: int,
    source_count: int,
    hardware_features: frozenset[str],
) -> None:
    required_ports = requirements.get("requires_ports")
    if required_ports is not None and ports < required_ports:
        raise CapabilityError(f"application {application!r} requires {required_ports} ports")
    required_sources = requirements.get("requires_sources")
    if required_sources is not None and source_count < required_sources:
        raise CapabilityError(f"application {application!r} requires {required_sources} sources")
    missing_hardware = set(requirements.get("requires_hardware", ())) - hardware_features
    if missing_hardware:
        raise CapabilityError(
            f"application {application!r} requires hardware features {sorted(missing_hardware)}"
        )


def _resolve_frequency_range(
    minimum_override: int | float | None,
    maximum_override: int | float | None,
) -> tuple[int | float, int | float]:
    minimum = (
        DEFAULT_FREQUENCY_MINIMUM_HZ
        if minimum_override is None
        else _frequency_value(minimum_override, "frequency_minimum_hz")
    )
    maximum = (
        DEFAULT_FREQUENCY_MAXIMUM_HZ
        if maximum_override is None
        else _frequency_value(maximum_override, "frequency_maximum_hz")
    )
    if minimum > maximum:
        raise CapabilityError("frequency_minimum_hz cannot exceed frequency_maximum_hz")
    return minimum, maximum


def _frequency_value(value: int | float, field_name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityError(f"{field_name} must be a positive finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0:
        raise CapabilityError(f"{field_name} must be a positive finite number")
    return int(numeric) if numeric.is_integer() else numeric


def detect_vna_model(*values: str) -> str | None:
    combined = " ".join(values).casefold()
    match = re.search(r"\b(vna-(?:2|4)-port)\b", combined)
    if match:
        return match.group(1)
    display_match = re.search(r"\bvirtual\s+vna\s+([24])\s+port\b", combined)
    return f"vna-{display_match.group(1)}-port" if display_match else None


def register_capability_commands(registry: CommandRegistry, capabilities: VNACapabilities) -> None:
    """Register model identity and the core virtual capability catalogs."""
    _register_query(
        registry,
        (HeaderNode("*OPT"),),
        lambda: ",".join(capabilities.option_query_tokens),
        common=True,
    )

    capability = (HeaderNode("SYSTem"), HeaderNode("CAPability"))
    _register_query(
        registry,
        (*capability, HeaderNode("FREQuency"), HeaderNode("MINimum")),
        lambda: str(capabilities.frequency_minimum),
    )
    _register_query(
        registry,
        (*capability, HeaderNode("FREQuency"), HeaderNode("MAXimum")),
        lambda: str(capabilities.frequency_maximum),
    )
    for endpoint, value in (
        ("MINimum", capabilities.frequency_minimum),
        ("MAXimum", capabilities.frequency_maximum),
    ):
        _register_query(
            registry,
            (*capability, HeaderNode("PRESet"), HeaderNode("FREQuency"), HeaderNode(endpoint)),
            lambda value=value: str(value),
        )

    hardware = (*capability, HeaderNode("HARDware"))
    ports = (*hardware, HeaderNode("PORTs"))

    def port_catalog() -> str:
        return ",".join(capabilities.port_names)

    _register_query(registry, (*ports, HeaderNode("CATalog")), port_catalog)
    _register_query(registry, (*ports, HeaderNode("COUNt")), lambda: str(capabilities.ports))
    internal = (*ports, HeaderNode("INTernal"))
    _register_query(registry, (*internal, HeaderNode("CATalog")), port_catalog)
    _register_query(registry, (*internal, HeaderNode("COUNt")), lambda: str(capabilities.ports))
    source_ports = (*ports, HeaderNode("SOURce"))
    _register_query(registry, (*source_ports, HeaderNode("CATalog")), port_catalog)
    _register_query(registry, (*source_ports, HeaderNode("COUNt")), lambda: str(capabilities.ports))
    source_internal = (*source_ports, HeaderNode("INTernal"))
    _register_query(registry, (*source_internal, HeaderNode("CATalog")), port_catalog)
    _register_query(
        registry, (*source_internal, HeaderNode("COUNt")), lambda: str(capabilities.ports)
    )

    _register_query(
        registry,
        (*hardware, HeaderNode("SOURce"), HeaderNode("COUNt")),
        lambda: str(capabilities.source_count),
    )
    receiver = (*hardware, HeaderNode("RECeiver"))
    _register_query(
        registry,
        (*receiver, HeaderNode("INTernal"), HeaderNode("COUNt")),
        lambda: str(capabilities.receiver_count),
    )
    _register_query(
        registry,
        (*receiver, HeaderNode("DACCess")),
        lambda: _boolean(capabilities.has_direct_receiver_access),
    )
    _register_query(
        registry,
        (*hardware, HeaderNode("LFEXtension"), HeaderNode("EXISts")),
        lambda: _boolean(capabilities.has_low_frequency_extension),
    )
    _register_attenuator_queries(registry, hardware, capabilities)

    licenses = (*capability, HeaderNode("LICenses"))
    registry.register(
        CommandSpec(
            path=(*licenses, HeaderNode("CATalog")),
            parameters=(
                ParameterSpec(
                    ParameterType.ENUM,
                    "license selection",
                    choices=("VALID", "ALL", "IGNORED"),
                ),
            ),
            handler=lambda invocation, selection: ",".join(capabilities.license_catalog(selection)),
            query=True,
        )
    )
    feature = (*licenses, HeaderNode("FEATure"))
    _register_query(
        registry,
        (*feature, HeaderNode("CATalog")),
        lambda: ",".join(capabilities.license_feature_names),
    )
    registry.register(
        CommandSpec(
            path=(*feature, HeaderNode("ENABle")),
            parameters=(ParameterSpec(ParameterType.STRING, "feature name"),),
            handler=lambda invocation, name: _boolean(capabilities.feature_enabled(name)),
            query=True,
        )
    )


def _register_attenuator_queries(
    registry: CommandRegistry,
    hardware: tuple[HeaderNode, ...],
    capabilities: VNACapabilities,
) -> None:
    parameter = (
        ParameterSpec(ParameterType.INTEGER, "port", minimum=1, maximum=capabilities.ports),
    )
    attenuator = (*hardware, HeaderNode("ATTenuator"))
    for kind, maximum in (("RECeiver", 35), ("SOURce", 65)):
        prefix = (*attenuator, HeaderNode(kind))
        for endpoint, response in (
            ("EXISts", lambda: _boolean(capabilities.has_attenuators)),
            (
                "MAXimum",
                lambda maximum=maximum: str(maximum if capabilities.has_attenuators else 0),
            ),
            ("STEP", lambda: "5" if capabilities.has_attenuators else "0"),
        ):
            registry.register(
                CommandSpec(
                    path=(*prefix, HeaderNode(endpoint)),
                    parameters=parameter,
                    handler=lambda invocation, port, response=response: response(),
                    query=True,
                )
            )


def _register_query(registry, path, response, *, common=False) -> None:
    registry.register(
        CommandSpec(
            path=path,
            handler=lambda invocation: response(),
            query=True,
            common=common,
        )
    )


def _load_capability_profile() -> dict[str, Any]:
    resource = files("scpi_emulator").joinpath("profiles/vna_capabilities.v1.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _normalize_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityError("VNA feature and application names must be non-empty strings")
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _token(value: str) -> str:
    return value.replace("_", "-").upper()


def _boolean(value: bool) -> str:
    return "1" if value else "0"
