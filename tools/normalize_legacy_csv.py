"""Normalize legacy instrument CSV files whose comma-containing fields were unquoted."""

import argparse
import csv
from pathlib import Path


COLUMNS = ["Equipment", "Port", "Command", "Response", "Validation"]
VALIDATION_PREFIXES = ("range:", "enum:")


def normalize_rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.reader(source))

    if not rows:
        raise ValueError(f"{path}: file is empty")

    normalized = [COLUMNS]
    for row_number, raw_row in enumerate(rows[1:], 2):
        cells = [cell.strip() for cell in raw_row]
        if not any(cells):
            continue
        if cells == ["```"]:
            continue
        if len(cells) < 4:
            raise ValueError(f"{path}: row {row_number} has fewer than four fields")

        equipment, port, command, response = cells[:4]
        tail = [cell for cell in cells[4:] if cell]
        validation = ""
        if tail and (tail[0] == "bool" or tail[0].startswith(VALIDATION_PREFIXES)):
            validation = ",".join(tail)
        elif tail:
            response = ",".join([response, *tail])

        normalized.append([equipment, port, command, response, validation])

    return normalized


def write_rows(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as destination:
        csv.writer(destination).writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--write", action="store_true", help="replace files in place")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    for path in args.files:
        rows = normalize_rows(path)
        widths = {len(row) for row in rows}
        print(f"{path}: {len(rows) - 1} data rows, widths={sorted(widths)}")
        if args.write:
            write_rows(path, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
