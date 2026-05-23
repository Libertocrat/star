"""Catalog builders for STAR public action/module discovery views."""

from __future__ import annotations

from typing import Iterable, Mapping

from star.actions.models.core import ActionSpec
from star.actions.models.presentation import ModuleSummary
from star.actions.presentation.serializers import to_action_summary
from star.actions.schemas.module import ModuleSpec


def _build_module_id(namespace: tuple[str, ...], module: str) -> str:
    """Build the fully qualified module identifier.

    Args:
        namespace: Module namespace segments.
        module: Bare module name.

    Returns:
        Dot-separated module identifier.
    """

    return ".".join((*namespace, module))


def _normalize(text: str) -> str:
    """Normalize free text used by catalog filters.

    Args:
        text: Input text to normalize.

    Returns:
        Lowercased and trimmed representation.
    """

    return text.lower().strip()


def _matches_query(text: str | None, query: str) -> bool:
    """Return whether a text value contains the normalized query.

    Args:
        text: Optional text field to evaluate.
        query: Normalized query term.

    Returns:
        True when query appears in the normalized text.
    """

    if not text:
        return False
    return query in _normalize(text)


def _matches_tags(
    action_tags: tuple[str, ...],
    requested_tags: tuple[str, ...],
    *,
    match: str,
) -> bool:
    """Return whether action tags satisfy the requested tag filter.

    Args:
        action_tags: Effective tags attached to one action summary.
        requested_tags: Normalized query tags.
        match: Matching behavior. `any` requires one match; `all` requires all.

    Returns:
        True when no tag filter is provided or when action tags satisfy the
        requested match behavior.
    """

    if not requested_tags:
        return True

    action_tag_set = {tag.lower() for tag in action_tags}
    requested_tag_set = {tag.lower() for tag in requested_tags}

    if match == "all":
        return requested_tag_set.issubset(action_tag_set)

    return bool(action_tag_set & requested_tag_set)


def _normalize_tags(tags_input: list[str] | None) -> tuple[str, ...]:
    """Normalize YAML tag lists into a deduplicated tuple.

    Args:
        tags_input: Raw YAML tag list from module metadata.

    Returns:
        Deduplicated and lowercased tag tuple preserving first appearance.
    """

    if tags_input is None:
        return ()

    tags: list[str] = []
    seen: set[str] = set()

    for token in tags_input:
        if not isinstance(token, str):
            continue
        normalized = token.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            tags.append(normalized)

    return tuple(tags)


def _collect_module_actions(
    action_specs: Iterable[ActionSpec],
    *,
    module_name: str,
    namespace: tuple[str, ...],
) -> list[ActionSpec]:
    """Collect action specs that belong to one module identity.

    Args:
        action_specs: Action specs available in the runtime registry.
        module_name: Bare module name.
        namespace: Namespace tuple for module identity.

    Returns:
        Action specs associated to the given module identity.
    """

    return [
        spec
        for spec in action_specs
        if spec.module == module_name and spec.namespace == namespace
    ]


def build_module_summaries(
    modules: list[ModuleSpec],
    actions: Mapping[str, ActionSpec],
) -> list[ModuleSummary]:
    """Build deterministic module summaries from module and action collections.

    Args:
        modules: Loaded ModuleSpec objects (ordered).
        actions: Mapping of runtime action name -> ActionSpec.

    Returns:
        Sorted list of public module summary models.
    """

    actions_by_name = actions

    results: list[ModuleSummary] = []

    for module in modules:
        namespace = module.namespace
        module_name = module.module
        module_id = _build_module_id(namespace, module_name)

        module_actions = _collect_module_actions(
            actions_by_name.values(),
            module_name=module_name,
            namespace=namespace,
        )

        summaries = [to_action_summary(action_spec) for action_spec in module_actions]

        results.append(
            ModuleSummary(
                module=module_name,
                module_id=module_id,
                namespace=".".join(namespace),
                namespace_path=namespace,
                description=module.description,
                tags=_normalize_tags(module.tags),
                authors=tuple(module.authors) if module.authors else None,
                actions=sorted(summaries, key=lambda action: action.action),
            )
        )

    return sorted(results, key=lambda module_summary: module_summary.module_id)


def filter_modules(
    modules: list[ModuleSummary],
    *,
    q: str | None = None,
    tags: tuple[str, ...] = (),
    match: str = "any",
) -> list[ModuleSummary]:
    """Apply optional query and tag filters to module summaries.

    Args:
        modules: Source module summaries.
        q: Optional free-text query.
        tags: Optional normalized effective action tag filters.
        match: Tag matching behavior. `any` matches at least one tag; `all`
            requires all tags.

    Returns:
        Filtered module summaries. For query and/or tag matches at action
        level, only matching actions are kept in each module summary.
    """

    if not q and not tags:
        return modules

    query = _normalize(q) if q else None

    filtered: list[ModuleSummary] = []

    for module in modules:
        candidate_actions = module.actions

        if tags:
            candidate_actions = [
                action
                for action in candidate_actions
                if _matches_tags(action.tags, tags, match=match)
            ]

        if query:
            candidate_actions = [
                action
                for action in candidate_actions
                if (
                    _matches_query(action.action, query)
                    or _matches_query(action.summary, query)
                    or _matches_query(action.description, query)
                    or any(_matches_query(tag_item, query) for tag_item in action.tags)
                )
            ]

        if candidate_actions:
            filtered.append(
                ModuleSummary(
                    module=module.module,
                    module_id=module.module_id,
                    namespace=module.namespace,
                    namespace_path=module.namespace_path,
                    description=module.description,
                    tags=module.tags,
                    authors=module.authors,
                    actions=candidate_actions,
                )
            )
            continue

        if query and not tags:
            if _matches_query(module.description, query) or _matches_query(
                module.module_id,
                query,
            ):
                filtered.append(module)

    return filtered


def get_action(actions: Mapping[str, ActionSpec], action_name: str) -> ActionSpec:
    """Return one action spec by fully qualified action name from mapping.

    Args:
        actions: Mapping of runtime action name -> ActionSpec.
        action_name: Fully qualified runtime action name.

    Returns:
        Runtime action specification.
    """

    return actions[action_name]
