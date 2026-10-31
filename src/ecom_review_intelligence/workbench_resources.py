"""Validated private-resource wiring for the explicit P7 local adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ecom_review_intelligence.agent_cli import _read_config, _validate_store_lineage
from ecom_review_intelligence.catalog import DEFAULT_CATALOG_LIMIT, load_catalog


def load_local_agent_resources(
    project_root: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, str]]:
    """Validate P6 lineage, then expose the Tier-A slice of the frozen catalog.

    The store always held every selected product; only the exposed set was three.
    The catalog keeps that decision in one place and preserves the three legacy
    codes so existing traces, scripts, and regression cases still resolve.
    """

    root = Path(project_root).resolve()
    config_path = root / "artifacts" / "manifests" / "phase-6-agent-config.json"
    store_path = root / "data" / "processed" / "ice_makers_evidence.sqlite"
    checkpoint_path = root / "data" / "processed" / "p7_workbench_checkpoints.sqlite"
    config = _read_config(config_path)
    _validate_store_lineage(store_path, config)
    entries = load_catalog(store_path, limit=DEFAULT_CATALOG_LIMIT)
    if not entries:
        raise ValueError("The local workbench requires at least one catalog product.")
    return (
        store_path,
        checkpoint_path,
        config,
        {entry.code: entry.parent_asin for entry in entries},
    )
