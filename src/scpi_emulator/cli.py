"""Console entry point for the SCPI emulator."""

from collections.abc import Sequence

from .emulator import main as run_emulator


def main(argv: Sequence[str] | None = None) -> int:
    """Run the emulator command-line interface."""
    return run_emulator(argv)

