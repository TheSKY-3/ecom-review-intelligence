"""Validated private-resource wiring for the explicit P7 local adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ecom_review_intelligence.agent_cli import _read_config, _validate_store_lineage


def load_local_agent_resources(
    project_root: Path,
) -> tuple[Path, Path, dict[str, Any], dict[str, str]]:
    """Reuse P6 config/lineage validation and select three private controlled products."""

    root = Path(project_root).resolve()
    config_path = root / "artifacts" / "manifests" / "phase-6-agent-config.json"
    store_path = root / "data" / "processed" / "ice_makers_evidence.sqlite"
    checkpoint_path = root / "data" / "processed" / "p7_workbench_checkpoints.sqlite"
    config = _read_config(config_path)
    _validate_store_lineage(store_path, config)
    with sqlite3.connect(f"file:{store_path.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT p.parent_asin
            FROM products p
            LEFT JOIN reviews r ON r.parent_asin = p.parent_asin
            GROUP BY p.parent_asin
            ORDER BY count(r.evidence_id) DESC, p.parent_asin
            LIMIT 3
            """
        ).fetchall()
    if len(rows) < 3:
        raise ValueError("The local workbench requires three controlled products.")
    codes = ("frostline", "northstar", "harbor")
    return (
        store_path,
        checkpoint_path,
        config,
        {code: row[0] for code, row in zip(codes, rows, strict=True)},
    )
