#!/usr/bin/env python3
"""Verify the installed all+dev dependency closure against reviewed licenses."""

from __future__ import annotations

import json
from collections import deque
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "licenses" / "dependencies.json"


def _dependency_closure(roots: list[str]) -> dict[str, metadata.Distribution]:
    environment = default_environment()
    pending = deque((canonicalize_name(name), frozenset()) for name in roots)
    found: dict[str, metadata.Distribution] = {}
    while pending:
        name, active_extras = pending.popleft()
        if name in found:
            continue
        distribution = metadata.distribution(name)
        found[name] = distribution
        for raw_requirement in distribution.requires or ():
            requirement = Requirement(raw_requirement)
            contexts = active_extras or frozenset({""})
            if requirement.marker and not any(
                requirement.marker.evaluate({**environment, "extra": extra}) for extra in contexts
            ):
                continue
            pending.append((canonicalize_name(requirement.name), frozenset(requirement.extras)))
    return found


def _detected_license(distribution: metadata.Distribution) -> str | None:
    package_metadata = distribution.metadata
    raw = package_metadata.get("License-Expression") or package_metadata.get("License")
    if raw:
        compact = " ".join(raw.split())
        aliases = {"MPL 2.0": "MPL-2.0", "MIT License": "MIT", "The MIT License": "MIT"}
        if compact in aliases:
            return aliases[compact]
        if compact.startswith("The MIT License Copyright"):
            return "MIT"
        if len(compact) < 80:
            return compact
    classifiers = "\n".join(package_metadata.get_all("Classifier", ()))
    for marker, identifier in (
        ("Mozilla Public License 2.0", "MPL-2.0"),
        ("GNU Lesser General Public License v2", "LGPL-2.1-or-later"),
        ("Apache Software License", "Apache-2.0"),
        ("BSD License", "BSD"),
        ("MIT License", "MIT"),
        ("Python Software Foundation License", "PSF-2.0"),
    ):
        if marker in classifiers:
            return identifier
    return None


def _reviewed_versions(entry: dict[str, object]) -> set[str]:
    """Return the explicitly reviewed package versions for an inventory entry."""
    versions = entry.get("reviewed_versions")
    if versions is not None:
        return {str(version) for version in versions}
    version = entry.get("reviewed_version")
    return {str(version)} if version is not None else set()


def main() -> int:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    requirements = inventory["policy"].get("requirements", {})
    packages = {canonicalize_name(name): entry for name, entry in inventory["packages"].items()}
    permitted = set(inventory["policy"]["permitted_licenses"])
    errors: list[str] = []

    if requirements.get("commercial_enterprise_use") is not True:
        errors.append("policy must require commercial and enterprise use")
    if requirements.get("mandatory_license_fee_or_royalty") is not False:
        errors.append("policy must prohibit mandatory license fees and royalties")

    for name, entry in packages.items():
        if entry["license"] not in permitted:
            errors.append(f"{name}: unapproved inventory license {entry['license']}")

    try:
        installed = _dependency_closure(inventory["roots"])
    except metadata.PackageNotFoundError as error:
        errors.append(f"declared dependency is not installed: {error.name}")
        installed = {}

    unknown = sorted(set(installed) - set(packages))
    if unknown:
        errors.append("unreviewed dependencies: " + ", ".join(unknown))

    for name, distribution in sorted(installed.items()):
        entry = packages.get(name)
        if entry is None:
            continue
        installed_version = distribution.version
        reviewed_versions = _reviewed_versions(entry)
        if installed_version not in reviewed_versions:
            errors.append(
                f"{name}: installed {installed_version}, reviewed "
                f"{', '.join(sorted(reviewed_versions)) or 'no versions'}"
            )
        detected = _detected_license(distribution)
        expected = entry["license"]
        if detected and detected != expected:
            if not (detected == "BSD" and expected.startswith("BSD-")):
                errors.append(f"{name}: metadata reports {detected}, inventory expects {expected}")
        metadata_note = f" (metadata: {detected})" if detected else ""
        print(f"{name} {installed_version}: {expected}{metadata_note}")

    if errors:
        print("\nLicense policy failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        f"\nRoyalty-free commercial/enterprise license policy passed for "
        f"{len(installed)} installed dependencies."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
