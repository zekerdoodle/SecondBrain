"""
Manifest reader + scope check for the safe-deploy tool.

The manifest at codebase/safe-deploy/manifest.yaml defines two safety
boundaries:

- `include:` names the positive rollback/deploy scope: code paths the
  machinery may consider.
- `exclude:` names live-state paths that deploy must reject and rollback must
  keep unreachable as defense in depth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

import pathspec
import yaml


REPO_ROOT = Path("/home/debian/second_brain")
MANIFEST_PATH = REPO_ROOT / "codebase" / "safe-deploy" / "manifest.yaml"


class ManifestError(RuntimeError):
    """Manifest is missing, malformed, or otherwise unusable."""


def _load_manifest(manifest_path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load the manifest YAML as a mapping."""
    if not manifest_path.exists():
        raise ManifestError(f"manifest missing: {manifest_path}")
    try:
        data = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as e:
        raise ManifestError(f"manifest YAML invalid: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"manifest root is not a mapping: {manifest_path}")
    return data


def _load_string_list(key: str, manifest_path: Path = MANIFEST_PATH) -> List[str]:
    """Load a required non-empty manifest list of path strings."""
    data = _load_manifest(manifest_path)
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ManifestError(
            f"manifest has no non-empty `{key}:` list: {manifest_path}"
        )
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ManifestError(
            f"manifest `{key}:` list must contain only non-empty strings: "
            f"{manifest_path}"
        )
    return list(value)


def load_include_paths(manifest_path: Path = MANIFEST_PATH) -> List[str]:
    """Load the manifest's required positive `include:` path list."""
    return _load_string_list("include", manifest_path)


def load_exclude_paths(manifest_path: Path = MANIFEST_PATH) -> List[str]:
    """Load the manifest's required live-state `exclude:` path list."""
    return _load_string_list("exclude", manifest_path)


def load_include_spec(manifest_path: Path = MANIFEST_PATH) -> pathspec.PathSpec:
    """Load the manifest's `include:` list as a compiled PathSpec.

    Raises ManifestError if the file is missing or has no `include:` list.
    """
    return pathspec.PathSpec.from_lines(
        "gitwildmatch", load_include_paths(manifest_path)
    )


def load_exclude_spec(manifest_path: Path = MANIFEST_PATH) -> pathspec.PathSpec:
    """Load the manifest's `exclude:` list as a compiled PathSpec.

    Raises ManifestError if the file is missing or has no `exclude:` list.
    """
    return pathspec.PathSpec.from_lines(
        "gitwildmatch", load_exclude_paths(manifest_path)
    )


def include_check(
    changed_paths: List[str], spec: pathspec.PathSpec
) -> Tuple[bool, List[str]]:
    """Check whether every changed path is inside the include spec.

    Returns (ok, violations) where violations is the list of paths that
    did not match the include list. ok is True iff violations is empty.
    """
    violations = [p for p in changed_paths if not spec.match_file(p)]
    return (not violations), violations


def scope_check(
    changed_paths: List[str], spec: pathspec.PathSpec
) -> Tuple[bool, List[str]]:
    """Check a list of changed paths against the exclude spec.

    Returns (ok, violations) where violations is the list of paths that
    matched the exclude list. ok is True iff violations is empty.
    """
    violations = [p for p in changed_paths if spec.match_file(p)]
    return (not violations), violations


def working_tree_clean_for_deploy(
    dirty_paths: List[str], spec: pathspec.PathSpec
) -> Tuple[bool, List[str]]:
    """Check whether the working tree's dirty paths are all manifest-excluded.

    A "clean enough" tree for deploy is one where every dirty path is in
    the manifest's exclude list -- i.e. live-state churn from the running
    server is tolerated, but in-scope code changes block the deploy and
    must be committed or reverted first.

    Returns (clean, surprises) where surprises is the list of dirty paths
    NOT in the exclude list. clean is True iff surprises is empty.
    """
    surprises = [p for p in dirty_paths if not spec.match_file(p)]
    return (not surprises), surprises
