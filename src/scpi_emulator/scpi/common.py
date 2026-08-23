"""Typed common identity, reset, self-test, and version commands."""

from __future__ import annotations

from collections.abc import Callable

from .registry import CommandRegistry, CommandSpec, HeaderNode


def register_common_commands(
    registry: CommandRegistry,
    identification: Callable[[], str],
    reset: Callable[[], str],
) -> None:
    """Register commands shared by every emulator instrument."""

    def common(name: str, handler, *, query: bool = False) -> None:
        registry.register(
            CommandSpec(
                path=(HeaderNode(name),),
                handler=lambda invocation: handler(),
                query=query,
                common=True,
            )
        )

    common("*IDN", identification, query=True)
    common("*RST", reset)
    common("*TST", lambda: "0", query=True)
    registry.register(
        CommandSpec(
            path=(HeaderNode("SYSTem"), HeaderNode("VERSion")),
            handler=lambda invocation: "1999.0",
            query=True,
        )
    )
