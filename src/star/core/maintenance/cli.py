"""Command-line entrypoint for conservative managed-storage maintenance."""

from __future__ import annotations

import argparse
import json
import sys

from star.core.config import get_settings
from star.core.maintenance.storage import (
    StorageMaintenanceError,
    StorageMaintenanceReport,
    inspect_storage,
    repair_storage,
)


def _parser() -> argparse.ArgumentParser:
    """Build the internal storage-maintenance argument parser."""

    parser = argparse.ArgumentParser(prog="star storage")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser(
        "inspect", help="Inspect managed storage without changes."
    )
    repair = commands.add_parser(
        "repair", help="Plan or apply conservative storage repair."
    )
    for command in (inspect, repair):
        command.add_argument("--json", action="store_true", dest="as_json")
        command.add_argument("--max-entries", type=int, default=10_000)
    repair.add_argument("--apply", action="store_true")
    repair.add_argument("--max-actions", type=int, default=100)
    return parser


def _print_report(report: StorageMaintenanceReport, *, as_json: bool) -> None:
    """Write a safe maintenance report in JSON or concise human form."""

    payload = report.to_dict()
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    print(f"Storage maintenance: {report.mode}")
    print(f"Entries scanned: {report.entries_scanned}")
    print(f"Findings: {len(report.findings)}")
    print(
        "Manual review: "
        f"{sum(outcome.value == 'manual_review' for outcome in report.outcomes)}"
    )
    for finding, outcome in zip(report.findings, report.outcomes, strict=True):
        identifier = str(finding.file_id) if finding.file_id else "unidentified"
        print(f"- {finding.code.value}: {identifier} ({outcome.value})")


def main(argv: list[str] | None = None) -> int:
    """Run the offline managed-storage inspection or repair command."""

    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
        if args.command == "inspect":
            report = inspect_storage(settings, max_entries=args.max_entries)
        else:
            report = repair_storage(
                settings,
                apply=args.apply,
                max_entries=args.max_entries,
                max_actions=args.max_actions,
            )
    except (BlockingIOError, StorageMaintenanceError, ValueError, OSError) as exc:
        print(f"Storage maintenance failed: {exc}", file=sys.stderr)
        return 1
    _print_report(report, as_json=args.as_json)
    return report.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
