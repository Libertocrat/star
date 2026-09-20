"""Coverage tests for the reviewed CORE binary invocation catalog."""

from __future__ import annotations

from pathlib import Path

from star.actions.build_engine.builder import build_actions
from star.actions.build_engine.loader import load_module_specs
from star.actions.build_engine.policy_enforcer import (
    compile_invocation_policy,
    enforce_build_policies,
)
from star.actions.build_engine.validator import validate_modules
from star.actions.engine_config import CORE_SPECS_DIR
from star.actions.models import InvocationTokenRole, SpecProvenance
from star.actions.runtime.policy_verifier import verify_rendered_invocation
from star.actions.runtime.renderer import render_command
from star.actions.schemas.module import ModuleSpec
from star.actions.security.binary_policies import BINARY_INVOCATION_POLICIES
from star.core.config import Settings

_EXPECTED_CORE_ACTION_BINARIES = {
    "base.analyze.count_file_chars": "wc",
    "base.analyze.count_file_lines": "wc",
    "base.analyze.count_file_words": "wc",
    "base.analyze.inspect_file_type": "file",
    "base.analyze.preview_file_end": "tail",
    "base.analyze.preview_file_start": "head",
    "base.analyze.search_file_lines": "grep",
    "base.crypto.decrypt_file_aes256": "openssl",
    "base.crypto.encrypt_file_aes256": "openssl",
    "base.crypto.hash_file": "openssl",
    "base.crypto.sha256_file": "sha256sum",
    "base.crypto.sha256_files": "sha256sum",
    "base.random.gen_bytes_file": "openssl",
    "base.random.gen_token_base64": "openssl",
    "base.random.gen_token_hex": "openssl",
    "base.random.gen_uuid": "cat",
}


def _load_core_modules(tmp_path: Path) -> tuple[list[ModuleSpec], Settings]:
    """Load and semantically validate the official CORE catalog.

    Args:
        tmp_path: Isolated STAR storage root.

    Returns:
        Validated CORE modules and their explicit settings snapshot.
    """

    settings = Settings.model_validate({"star_root_dir": str(tmp_path)})
    modules = load_module_specs([CORE_SPECS_DIR], settings)
    validate_modules(modules)
    return modules, settings


def _action_name(module: ModuleSpec, action_name: str) -> str:
    """Return the final action name for one loaded module action."""

    return ".".join((*module.namespace, module.module, action_name))


# ============================================================================
# Official CORE Coverage
# ============================================================================


def test_every_official_core_action_matches_a_reviewed_invocation_form(tmp_path):
    """
    GIVEN the complete official CORE YAML catalog
    WHEN each action is matched against its provenance-scoped binary policy
    THEN every known action compiles to an explicit reviewed CORE form
    """
    modules, _ = _load_core_modules(tmp_path)

    compiled = {
        _action_name(module, action_name): compile_invocation_policy(
            module,
            action_name,
            action,
        )
        for module in modules
        for action_name, action in module.actions.items()
    }

    assert {name: policy.binary for name, policy in compiled.items()} == (
        _EXPECTED_CORE_ACTION_BINARIES
    )
    assert all(
        policy.authorization.provenance is SpecProvenance.CORE
        for policy in compiled.values()
    )


def test_core_catalog_compiles_typed_openssl_and_cat_tokens(tmp_path):
    """
    GIVEN CORE OpenSSL and cat forms with subcommands and wrapped operands
    WHEN their reviewed policies are compiled
    THEN literals, finite options, outputs, and secret files retain typed roles
    """
    modules, _ = _load_core_modules(tmp_path)
    compiled = {
        _action_name(module, action_name): compile_invocation_policy(
            module,
            action_name,
            action,
        )
        for module in modules
        for action_name, action in module.actions.items()
    }

    digest = compiled["base.crypto.hash_file"]
    assert tuple(token.role for token in digest.template_tokens) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.LITERAL,
        InvocationTokenRole.OPTION,
        InvocationTokenRole.MANAGED_INPUT,
    )
    assert digest.template_tokens[1].exact_value == "dgst"
    assert digest.template_tokens[2].allowed_values == (
        "-blake2b512",
        "-blake2s256",
        "-md5",
        "-sha1",
        "-sha224",
        "-sha256",
        "-sha384",
        "-sha512",
    )

    encrypt = compiled["base.crypto.encrypt_file_aes256"]
    secret = encrypt.template_tokens[-1]
    assert secret.role is InvocationTokenRole.SECRET_FILE
    assert secret.value_prefix == "file:"
    assert any(
        token.role is InvocationTokenRole.MANAGED_OUTPUT
        for token in encrypt.template_tokens
    )

    uuid_policy = compiled["base.random.gen_uuid"]
    assert uuid_policy.template_tokens[1].role is InvocationTokenRole.LITERAL
    assert uuid_policy.template_tokens[1].exact_value == "/proc/sys/kernel/random/uuid"

    grep_policy = compiled["base.analyze.search_file_lines"]
    assert grep_policy.template_tokens[-3].exact_value == "-e"
    assert grep_policy.template_tokens[-2].role is InvocationTokenRole.PATTERN
    assert grep_policy.template_tokens[-2].max_length == 4096

    sha256_files = compiled["base.crypto.sha256_files"]
    assert sha256_files.template_tokens[-1].max_count == 32


def test_catalog_coverage_activates_core_invocation_enforcement(tmp_path):
    """
    GIVEN official CORE actions covered by the reviewed policy catalog
    WHEN the build pipeline compiles the runtime registry
    THEN every CORE ActionSpec receives its provenance-authorized policy
    """
    modules, settings = _load_core_modules(tmp_path)

    catalog_policy = enforce_build_policies(modules, settings)
    actions = build_actions(modules, catalog_policy)

    assert set(catalog_policy.invocation_policies) == set(
        _EXPECTED_CORE_ACTION_BINARIES
    )
    assert all(
        spec.invocation_policy.authorization.provenance is SpecProvenance.CORE
        for spec in actions.values()
    )


def test_core_action_renders_and_verifies_typed_policy_tokens(tmp_path):
    """
    GIVEN an official CORE action with an exact reviewed literal operand
    WHEN its empty parameter set is rendered and verified before execution
    THEN the runtime enforces typed policy tokens without a CORE bypass
    """
    modules, settings = _load_core_modules(tmp_path)
    catalog_policy = enforce_build_policies(modules, settings)
    actions = build_actions(modules, catalog_policy)
    spec = actions["base.random.gen_uuid"]

    rendered = render_command(spec, {}, settings=settings)

    assert tuple(token.role for token in rendered.tokens) == (
        InvocationTokenRole.BINARY,
        InvocationTokenRole.LITERAL,
    )
    verify_rendered_invocation(rendered, spec, settings=settings)


# ============================================================================
# Provenance Scope
# ============================================================================


def test_extension_visible_forms_require_explicit_capabilities():
    """
    GIVEN every reviewed binary invocation form in the catalog
    WHEN a form authorizes EXTENSION provenance
    THEN it names an explicit extension capability and OpenSSL remains CORE-only
    """
    forms = tuple(
        form for policy in BINARY_INVOCATION_POLICIES.values() for form in policy.forms
    )

    extension_authorizations = tuple(
        authorization
        for form in forms
        for authorization in form.authorizations
        if authorization.provenance is SpecProvenance.EXTENSION
    )

    assert all(
        authorization.required_capabilities
        for authorization in extension_authorizations
    )
    assert all(
        all(
            authorization.provenance is not SpecProvenance.EXTENSION
            for authorization in form.authorizations
        )
        for form in BINARY_INVOCATION_POLICIES["openssl"].forms
    )
