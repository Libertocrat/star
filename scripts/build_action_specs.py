"""Manual helper script to inspect parsed STAR DSL module specs."""

from pathlib import Path
from pprint import pprint

from star.actions.build_engine.builder import build_actions
from star.actions.build_engine.loader import load_module_specs
from star.actions.build_engine.policy_enforcer import enforce_build_policies
from star.actions.build_engine.validator import validate_modules
from star.core.config import Settings


def main() -> int:
    """Load core STAR DSL specs and print a readable summary.

    Returns:
        Zero when the catalog compiles, or one when a stage fails.
    """

    specs_dir = Path("src/star/actions/specs")
    settings = Settings.model_validate(
        {
            "star_api_token": "InspectionOnlyToken123!",
            "star_root_dir": "/tmp/star",  # noqa: S108 -- fixed path for testing purposes
            "star_max_yml_bytes": 100 * 1024,
        }
    )

    try:
        modules = load_module_specs([specs_dir], settings)
    except Exception as e:
        print(f"Failed to load module specs: {e}")
        return 1

    print("\n=== MODULES LOADED ===\n")

    for module in modules:
        print(f"Module: {module.module}")
        print(f"Version: {module.version}")
        print(f"Binaries: {module.binaries}")
        print(f"Actions: {list(module.actions.keys())}")
        print()

    print("\n=== DETAILED MODULES ===\n")

    for module in modules:
        print(f"Module: {module.module}")
        pprint(module.model_dump(), depth=5)
        print()

    print("\n=== VALIDATING MODULES ===\n")
    try:
        validate_modules(modules)
        print("All modules are valid.")
    except Exception:
        print("Module validation failed")
        return 1

    try:
        compiled_actions = build_actions(
            modules,
            enforce_build_policies(modules, settings),
        )
        print("\n=== COMPILED ACTIONS ===\n")
        for action_name, action_spec in compiled_actions.items():
            print(f"Action: {action_name}")
            pprint(action_spec.model_dump(), depth=8)
            print()
    except Exception:
        print("Action compilation failed")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
