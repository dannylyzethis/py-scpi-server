"""Model-faithful PNA hardware, option, and license capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from .registry import CommandRegistry, CommandSpec, HeaderNode, ParameterSpec, ParameterType


DEFAULT_HARDWARE_CONFIGURATION = {"N5222B": "200", "N5242B": "201"}

OPTION_QUERY_CODES: dict[str, tuple[str, ...]] = {
    "S93007A": ("007",),
    "S93007B": ("007",),
    "S93010A": ("010",),
    "S93010B": ("010",),
    "S93011A": ("011",),
    "S93011B": ("011",),
    "S93015A": ("015",),
    "S93015B": ("015",),
    "S93025A": ("025",),
    "S93025B": ("025",),
    "S93026A": ("008",),
    "S93026B": ("008",),
    "S93029A": ("028", "080"),
    "S93029B": ("028", "080"),
    "S93080A": ("080",),
    "S93080B": ("080",),
    "S93082A": ("082", "080"),
    "S93082B": ("082", "080"),
    "S93083A": ("083", "080"),
    "S93083B": ("083", "080"),
    "S93084A": ("084", "080"),
    "S93084B": ("084", "080"),
    "S93086A": ("086", "080"),
    "S93086B": ("086", "080"),
    "S93087A": ("087", "080"),
    "S93087B": ("087", "080"),
    "S93088A": ("088",),
    "S93088B": ("088",),
    "S93089A": ("089", "080"),
    "S93089B": ("089", "080"),
    "S930902A": ("090", "902"),
    "S930902B": ("090", "902"),
    "S93118A": ("118",),
    "S93118B": ("118",),
    "S93460A": ("460",),
    "S93460B": ("460",),
    "S93551A": ("551",),
    "S93551B": ("551",),
    "S93898A": ("898",),
    "S93898B": ("898",),
}


class CapabilityError(ValueError):
    """Raised when a requested PNA configuration cannot exist."""


@dataclass(frozen=True)
class PNACapabilities:
    model: str
    instrument_class: str
    hardware_configuration: str
    hardware_addons: tuple[str, ...]
    application_options: tuple[str, ...]
    application_features: tuple[tuple[str, str], ...]
    ports: int
    sources: int
    features: frozenset[str]
    frequency_minimum: int
    frequency_maximum: int
    serial: str
    firmware: str

    @classmethod
    def create(
        cls,
        model: str,
        *,
        hardware_configuration: str | None = None,
        hardware_addons: tuple[str, ...] = (),
        application_options: tuple[str, ...] = (),
        serial: str = "US12345678",
        firmware: str = "A.20.25.04",
    ) -> "PNACapabilities":
        matrix = _load_compatibility_matrix()
        model = model.upper()
        models = matrix["models"]
        if model not in models:
            raise CapabilityError(f"unsupported PNA model {model!r}")
        model_data = models[model]
        configuration = hardware_configuration or DEFAULT_HARDWARE_CONFIGURATION[model]
        configurations = model_data["hardware_configurations"]
        if configuration not in configurations:
            raise CapabilityError(
                f"hardware configuration {configuration!r} is not available on {model}"
            )
        requested_addons = tuple(dict.fromkeys(option.upper() for option in hardware_addons))
        unknown_addons = set(requested_addons) - set(model_data["hardware_addons"])
        if unknown_addons:
            raise CapabilityError(f"hardware add-ons are not available on {model}: {sorted(unknown_addons)}")
        if "XSB" in requested_addons and configuration not in {"422", "423"}:
            raise CapabilityError("XSB requires N5242B hardware configuration 422 or 423")

        requested_apps = tuple(dict.fromkeys(option.upper() for option in application_options))
        hardware = configurations[configuration]
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
        return cls(
            model=model,
            instrument_class=model_data["instrument_class"],
            hardware_configuration=configuration,
            hardware_addons=requested_addons,
            application_options=requested_apps,
            application_features=tuple(application_features),
            ports=hardware["ports"],
            sources=hardware["sources"],
            features=frozenset(hardware["features"]),
            frequency_minimum=frequency["minimum"],
            frequency_maximum=frequency["maximum"],
            serial=serial,
            firmware=firmware,
        )

    @property
    def identification(self) -> str:
        return f"Keysight Technologies,{self.model},{self.serial},{self.firmware}"

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


def detect_pna_model(*values: str) -> str | None:
    combined = " ".join(values).upper()
    match = re.search(r"\b(N5222B|N5242B)\b", combined)
    return match.group(1) if match else None


def register_capability_commands(
    registry: CommandRegistry, capabilities: PNACapabilities
) -> None:
    """Register model identity and the core Keysight capability catalogs."""
    _register_query(registry, (HeaderNode("*IDN"),), lambda: capabilities.identification, common=True)
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
    constraint = application.get("constraint", "").casefold()
    if "four-port" in constraint and hardware["ports"] != 4:
        raise CapabilityError(f"application {application_id!r} requires four ports")
    if "two-source" in constraint and hardware["sources"] != 2:
        raise CapabilityError(f"application {application_id!r} requires two sources")


def _software_requirement_matches(requirement: str, option: str) -> bool:
    if requirement.endswith("A/B"):
        prefix = requirement[:-3]
        return option in {f"{prefix}A", f"{prefix}B"}
    return option == requirement


def _feature_name(application_id: str) -> str:
    return application_id.replace("_", " ").title()


def _fallback_option_code(option: str) -> str:
    match = re.fullmatch(r"S93(\d+)[AB]", option)
    return match.group(1)[-3:] if match else option


def _boolean(value: bool) -> str:
    return "1" if value else "0"
