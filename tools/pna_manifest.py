"""Generate a VNA documented-command coverage report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scpi_emulator.emulator import SCPIInstrument
from scpi_emulator.scpi import (
    coverage_report,
    load_command_manifest,
    registry_implementation_keys,
)


def implementation_keys(instrument: SCPIInstrument) -> frozenset[str]:
    """Collect typed commands plus literal built-in legacy commands."""
    typed = registry_implementation_keys(instrument.core_registry)
    legacy = frozenset(
        key.upper()
        for key in instrument.commands
        if not any(marker in key for marker in ("(", "[", "\\"))
    )
    return typed | legacy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--model", choices=("N5222B-EMU", "N5242B-EMU"), default="N5222B-EMU")
    parser.add_argument("--firmware", default="E.1.0")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-gaps",
        action="store_true",
        help="return success after writing a report that contains documented gaps",
    )
    args = parser.parse_args(argv)

    manifest = load_command_manifest(args.manifest)
    instrument = SCPIInstrument(f"Virtual {args.model}", args.model)
    report = coverage_report(
        manifest,
        implementation_keys(instrument),
        model=args.model,
        firmware=args.firmware,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 1 if report["summary"]["missing"] and not args.allow_gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
