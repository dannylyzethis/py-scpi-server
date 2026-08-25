"""Model-faithful VNA hardware, option, and license capabilities."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from importlib.resources import files
from typing import Any

from .registry import CommandRegistry, CommandSpec, HeaderNode, ParameterSpec, ParameterType


DEFAULT_HARDWARE_CONFIGURATION = {"N5222B-EMU": "200", "N5242B-EMU": "201"}
DEVELOPER_HARDWARE_CONFIGURATION = {"N5222B-EMU": "419", "N5242B-EMU": "425"}
DEVELOPER_HARDWARE_ADDONS = {"N5222B-EMU": ("021",), "N5242B-EMU": ("021",)}

OPTION_QUERY_CODES: dict[str, tuple[str, ...]] = {
    "E93007A": ("007",),
    "E93007B": ("007",),
    "E93010A": ("010",),
    "E93010B": ("010",),
    "E93011A": ("011",),
    "E93011B": ("011",),
    "E93015A": ("015",),
    "E93015B": ("015",),
    "E93025A": ("025",),
    "E93025B": ("025",),
    "E93026A": ("008",),
    "E93026B": ("008",),
    "E93029A": ("028", "080"),
    "E93029B": ("028", "080"),
    "E93080A": ("080",),
    "E93080B": ("080",),
    "E93082A": ("082", "080"),
    "E93082B": ("082", "080"),
    "E93083A": ("083", "080"),
    "E93083B": ("083", "080"),
    "E93084A": ("084", "080"),
    "E93084B": ("084", "080"),
    "E93086A": ("086", "080"),
    "E93086B": ("086", "080"),
    "E93087A": ("087", "080"),
    "E93087B": ("087", "080"),
    "E93088A": ("088",),
    "E93088B": ("088",),
    "E93089A": ("089", "080"),
    "E93089B": ("089", "080"),
    "E930902A": ("090", "902"),
    "E930902B": ("090", "902"),
    "E93118A": ("118",),
    "E93118B": ("118",),
    "E93460A": ("460",),
    "E93460B": ("460",),
    "E93551A": ("551",),
    "E93551B": ("551",),
    "E93898A": ("898",),
    "E93898B": ("898",),
}


class CapabilityError(ValueError):
    """Raised when a requested VNA configuration cannot exist."""


class CompatibilityMode(str, Enum):
    """Policy used to choose installed VNA application capabilities."""

    MODEL_FAITHFUL = "model-faithful"
    ALL_APPLICATIONS = "all-applications"


@dataclass(frozen=True)
class PNACapabilities:
    mode: CompatibilityMode
    model: str
    instrument_class: str
    hardware_configuration: str
    hardware_addons: tuple[str, ...]
    application_options: tuple[str, ...]
    application_features: tuple[tuple[str, str], ...]
    ports: int
    sources: int
    features: frozenset[str]
    frequency_minimum: int | float
    frequency_maximum: int | float
    serial: str
    firmware: str

    @classmethod
    def create(
        cls,
        model: str,
        *,
        mode: CompatibilityMode | str = CompatibilityMode.MODEL_FAITHFUL,
        hardware_configuration: str | None = None,
        hardware_addons: tuple[str, ...] | None = None,
        application_options: tuple[str, ...] | None = None,
        frequency_minimum_hz: int | float | None = None,
        frequency_maximum_hz: int | float | None = None,
        serial: str = "US12345678",
        firmware: str = "E.1.0",
    ) -> "PNACapabilities":
        matrix = _load_compatibility_matrix()
        model = model.upper()
        try:
            mode = CompatibilityMode(mode)
        except ValueError as error:
            choices = ", ".join(item.value for item in CompatibilityMode)
            raise CapabilityError(f"unsupported compatibility mode {mode!r}; choose {choices}") from error
        models = matrix["models"]
        if model not in models:
            raise CapabilityError(f"unsupported VNA model {model!r}")
        model_data = models[model]
        default_configurations = (
            DEVELOPER_HARDWARE_CONFIGURATION
            if mode is CompatibilityMode.ALL_APPLICATIONS
            else DEFAULT_HARDWARE_CONFIGURATION
        )
        configuration = hardware_configuration or default_configurations[model]
        configurations = model_data["hardware_configurations"]
        if configuration not in configurations:
            raise CapabilityError(
                f"hardware configuration {configuration!r} is not available on {model}"
            )
        if hardware_addons is None:
            hardware_addons = (
                DEVELOPER_HARDWARE_ADDONS[model]
                if mode is CompatibilityMode.ALL_APPLICATIONS
                else ()
            )
        requested_addons = tuple(dict.fromkeys(option.upper() for option in hardware_addons))
        unknown_addons = set(requested_addons) - set(model_data["hardware_addons"])
        if unknown_addons:
            raise CapabilityError(f"hardware add-ons are not available on {model}: {sorted(unknown_addons)}")
        if "XSB" in requested_addons and configuration not in {"422", "423"}:
            raise CapabilityError("XSB requires N5242B-EMU hardware configuration 422 or 423")

        hardware = configurations[configuration]
        explicit_apps = tuple(
            dict.fromkeys(option.upper() for option in (application_options or ()))
        )
        if mode is CompatibilityMode.ALL_APPLICATIONS:
            automatic_apps = _compatible_application_options(
                matrix["applications"], model, configuration, requested_addons, hardware
            )
            requested_apps = tuple(dict.fromkeys((*automatic_apps, *explicit_apps)))
        else:
            requested_apps = explicit_apps
        application_features: list[tuple[str, str]] = []
        for option in requested_apps:
            application_id = _application_for_option(matrix["applications"], model, option)
            if application_id is None:
                raise CapabilityError(f"application option {option!r} is not available on {model}")
            _validate_application_requirements(
                matrix["applications"][application_id],
                application_id,
                configuration,
                requested_addons,
                requested_apps,
                hardware,
            )
            application_features.append((application_id, _feature_name(application_id)))

        frequency = model_data["frequency_hz"]
        frequency_minimum, frequency_maximum = _resolve_frequency_range(
            frequency,
            frequency_minimum_hz,
            frequency_maximum_hz,
        )
        return cls(
            mode=mode,
            model=model,
            instrument_class=model_data["instrument_class"],
            hardware_configuration=configuration,
            hardware_addons=requested_addons,
            application_options=requested_apps,
            application_features=tuple(application_features),
            ports=hardware["ports"],
            sources=hardware["sources"],
            features=frozenset(hardware["features"]),
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
        return self.ports + self.sources

    @property
    def has_attenuators(self) -> bool:
        return {"source_attenuators", "receiver_attenuators"} <= self.features

    @property
    def has_direct_receiver_access(self) -> bool:
        return bool({"front_panel_access", "configurable_test_set"} & self.features)

    @property
    def has_low_frequency_extension(self) -> bool:
        return "low_frequency_extension" in self.features

    @property
    def option_query_codes(self) -> tuple[str, ...]:
        codes = [self.hardware_configuration, *self.hardware_addons]
        for option in self.application_options:
            codes.extend(OPTION_QUERY_CODES.get(option, (_fallback_option_code(option),)))
        return tuple(dict.fromkeys(codes))

    def license_catalog(self, selection: str) -> tuple[str, ...]:
        if selection.upper() == "IGNORED":
            return ()
        licenses = [f"{self.model}-{self.hardware_configuration}"]
        licenses.extend(f"{self.model}-{option}" for option in self.hardware_addons)
        licenses.extend(f"{option}-1FP" for option in self.application_options)
        return tuple(licenses)

    @property
    def license_feature_names(self) -> tuple[str, ...]:
        return tuple(feature for _, feature in self.application_features)

    def feature_enabled(self, name: str) -> bool:
        normalized = name.casefold()
        return any(
            normalized in {application_id.casefold(), feature.casefold()}
            for application_id, feature in self.application_features
        )

    @property
    def command_capabilities(self) -> frozenset[str]:
        """Names application command specifications can use as availability gates."""
        names: set[str] = set()
        for application_id, feature in self.application_features:
            names.update(
                {
                    application_id,
                    application_id.replace("_", "-"),
                    feature.casefold(),
                }
            )
        names.update(option.casefold() for option in self.application_options)
        return frozenset(names)


def _resolve_frequency_range(
    model_frequency: dict[str, int],
    minimum_override: int | float | None,
    maximum_override: int | float | None,
) -> tuple[int | float, int | float]:
    model_minimum = model_frequency["minimum"]
    model_maximum = model_frequency["maximum"]
    minimum = (
        model_minimum
        if minimum_override is None
        else _frequency_value(minimum_override, "frequency_minimum_hz")
    )
    maximum = (
        model_maximum
        if maximum_override is None
        else _frequency_value(maximum_override, "frequency_maximum_hz")
    )
    if minimum < model_minimum:
        raise CapabilityError(
            f"frequency_minimum_hz cannot be below the {model_minimum} Hz model minimum"
        )
    if maximum > model_maximum:
        raise CapabilityError(
            f"frequency_maximum_hz cannot exceed the {model_maximum} Hz model maximum"
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


def detect_pna_model(*values: str) -> str | None:
    combined = " ".join(values).upper()
    match = re.search(r"\b(N5222B-EMU|N5242B-EMU)\b", combined)
    return match.group(1) if match else None


def register_capability_commands(
    registry: CommandRegistry, capabilities: PNACapabilities
) -> None:
    """Register model identity and the core Virtual capability catalogs."""
    _register_query(
        registry,
        (HeaderNode("*OPT"),),
        lambda: ",".join(capabilities.option_query_codes),
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
    _register_query(registry, (*source_internal, HeaderNode("COUNt")), lambda: str(capabilities.ports))

    _register_query(
        registry,
        (*hardware, HeaderNode("SOURce"), HeaderNode("COUNt")),
        lambda: str(capabilities.sources),
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
            handler=lambda invocation, selection: ",".join(
                capabilities.license_catalog(selection)
            ),
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
    capabilities: PNACapabilities,
) -> None:
    parameter = (ParameterSpec(ParameterType.INTEGER, "port", minimum=1, maximum=capabilities.ports),)
    attenuator = (*hardware, HeaderNode("ATTenuator"))
    for kind, maximum in (("RECeiver", 35), ("SOURce", 65)):
        prefix = (*attenuator, HeaderNode(kind))
        for endpoint, response in (
            ("EXISts", lambda: _boolean(capabilities.has_attenuators)),
            ("MAXimum", lambda maximum=maximum: str(maximum if capabilities.has_attenuators else 0)),
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


def _load_compatibility_matrix() -> dict[str, Any]:
    resource = files("scpi_emulator").joinpath("profiles/pna_compatibility.v1.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _application_for_option(applications: dict[str, Any], model: str, option: str) -> str | None:
    for application_id, application in applications.items():
        if model in application["models"] and option in application["options"]:
            return application_id
    return None


def _compatible_application_options(
    applications: dict[str, Any],
    model: str,
    configuration: str,
    addons: tuple[str, ...],
    hardware: dict[str, Any],
) -> tuple[str, ...]:
    """Select one current license per application that fits the physical profile."""
    candidates = tuple(
        _preferred_option(application["options"])
        for application in applications.values()
        if model in application["models"]
    )
    compatible: list[str] = []
    for option in candidates:
        application_id = _application_for_option(applications, model, option)
        if application_id is None:
            continue
        try:
            _validate_application_requirements(
                applications[application_id],
                application_id,
                configuration,
                addons,
                candidates,
                hardware,
            )
        except CapabilityError:
            continue
        compatible.append(option)
    return tuple(compatible)


def _preferred_option(options: list[str]) -> str:
    """Prefer the current B-generation license when a matrix lists A and B products."""
    return next((option for option in options if option.endswith("B")), options[-1])


def _validate_application_requirements(
    application: dict[str, Any],
    application_id: str,
    configuration: str,
    addons: tuple[str, ...],
    applications: tuple[str, ...],
    hardware: dict[str, Any],
) -> None:
    installed_hardware = {configuration, *addons}
    required_hardware = set(application.get("requires_any_hardware", ()))
    if required_hardware and not required_hardware & installed_hardware:
        raise CapabilityError(
            f"application {application_id!r} requires one of {sorted(required_hardware)}"
        )
    excluded_hardware = set(application.get("excludes_hardware", ()))
    if excluded_hardware & installed_hardware:
        raise CapabilityError(
            f"application {application_id!r} is not available with {configuration}"
        )
    for requirement in application.get("requires_software", ()):
        if not any(_software_requirement_matches(requirement, option) for option in applications):
            raise CapabilityError(
                f"application {application_id!r} requires software {requirement}"
            )
    if application.get("requires_ports") not in (None, hardware["ports"]):
        raise CapabilityError(f"application {application_id!r} requires four ports")
    if application.get("requires_sources") not in (None, hardware["sources"]):
        raise CapabilityError(f"application {application_id!r} requires two sources")


def _software_requirement_matches(requirement: str, option: str) -> bool:
    if requirement.endswith("A/B"):
        prefix = requirement[:-3]
        return option in {f"{prefix}A", f"{prefix}B"}
    return option == requirement


def _feature_name(application_id: str) -> str:
    return application_id.replace("_", " ").title()


def _fallback_option_code(option: str) -> str:
    match = re.fullmatch(r"E93(\d+)[AB]", option)
    return match.group(1)[-3:] if match else option


def _boolean(value: bool) -> str:
    return "1" if value else "0"
