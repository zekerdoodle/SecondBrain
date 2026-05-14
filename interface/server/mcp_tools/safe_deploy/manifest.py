"""
Manifest reader + scope check for the safe-deploy tool.

The manifest at codebase/safe-deploy/manifest.yaml defines which paths are
deploy-scope (everything not in `exclude:`) vs. live-state paths that must
not be touched by deploy machinery.

Phase A consumes only the `exclude:` list. Phase C will add a positive
`include:` list per the project's design decisions; this module's API is
shaped so that addition is additive (a new `load_include_spec` and
`pathspec_for_include`), not a rewrite.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pathspec
import yaml


REPO_ROOT = Path("/home/debian/second_brain")
MANIFEST_PATH = REPO_ROOT / "codebase" / "safe-deploy" / "manifest.yaml"


class ManifestError(RuntimeError):
    """Manifest is missing, malformed, or otherwise unusable."""


def load_exclude_spec(manifest_path: Path = MANIFEST_PATH) -> pathspec.PathSpec:
    """Load the manifest's `exclude:` list as a compiled PathSpec.

    Raises ManifestError if the file is missing or has no `exclude:` list.
    """
    if not manifest_path.exists():
        raise ManifestError(f"manifest missing: {manifest_path}")
    try:
        data = yaml.safe_load(manifest_path.read_text())
    except yaml.YAMLError as e:
        raise ManifestError(f"manifest YAML invalid: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"manifest root is not a mapping: {manifest_path}")
    exclude = data.get("exclude")
    if not isinstance(exclude, list) or not exclude:
        raise ManifestError(
            f"manifest has no non-empty `exclude:` list: {manifest_path}"
        )
    return pathspec.PathSpec.from_lines("gitwildmatch", exclude)


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
    the manifest's exclude list — i.e. live-state churn from the running
    server is tolerated, but in-scope code changes block the deploy and
    must be committed or reverted first.

    Returns (clean, surprises) where surprises is the list of dirty paths
    NOT in the exclude list. clean is True iff surprises is empty.
    """
    surprises = [p for p in dirty_paths if not spec.match_file(p)]
    return (not surprises), surprises
