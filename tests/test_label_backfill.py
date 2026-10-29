import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from ecom_review_intelligence.engine import compute_evidence_id, initialize_store
from ecom_review_intelligence.label_backfill import (
    check,
    collect_unjudged_pool,
    merge_answers,
    relation,
    star_proxy,
)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

RULES_TEXT = "# 判定规则\n\n只判指定维度的方向。\n"


def _store_fixture(tmp_path: Path) -> tuple[Path, str]:
    store = tmp_path / "evidence.sqlite"
    snapshot = "a" * 64
    initialize_store(
        store,
        snapshot_id=snapshot,
        contract_sha256="b" * 64,
        aspect_terms={"noise": ("noise",), "leakage": ("leak",)},
        polarity_terms={"supporting": ("quiet",), "opposing": ("loud",)},
    )
    reviews = [
        (1, "noise without any direction word", 1.0),
        (2, "lots of noise overnight", 5.0),
        (3, "noise hum", None),
        (4, "leak puddle", 2.0),
        (5, "very quiet, no noise at all", 5.0),
    ]
    with sqlite3.connect(store) as connection:
        connection.execute("INSERT INTO products(parent_asin,title) VALUES (?,?)", ("P1", "t"))
        for line, text, rating in reviews:
            connection.execute(
                "INSERT INTO reviews(evidence_id,source_line,parent_asin,text,rating,timestamp,verified_purchase,helpful_vote)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (compute_evidence_id(snapshot, line), line, "P1", text, rating, line, 1, 0),
            )
    return store, snapshot


def test_collect_unjudged_pool_keeps_only_lexical_blind_items(tmp_path):
    store, _ = _store_fixture(tmp_path)
    pool = collect_unjudged_pool(store)
    assert {aspect: [item["id"] for item in items] for aspect, items in pool.items()} == {
        "noise": ["noise:1", "noise:2", "noise:3"],
        "leakage": ["leakage:4"],
    }
    assert pool["noise"][0]["rating"] == 1.0 and pool["noise"][2]["rating"] is None
    exclude = {"noise:1", "leakage:4"}
    remaining = collect_unjudged_pool(store, exclude_ids=exclude)
    assert [item["id"] for item in remaining["noise"]] == ["noise:2", "noise:3"]
    assert remaining["leakage"] == []


def test_export_package_hides_ratings_from_handoffs(tmp_path):
    store, _ = _store_fixture(tmp_path)
    rules = tmp_path / "rules.md"
    rules.write_text(RULES_TEXT, encoding="utf-8")
    from ecom_review_intelligence.label_backfill import export_package

    summary = export_package(store, tmp_path / "pkg", rules, batch_size=2)
    assert summary["total_items"] == 4
    assert summary["per_aspect_items"] == {"leakage": 1, "noise": 3}
    batches = sorted((tmp_path / "pkg" / "handoffs").rglob("*.md"))
    assert len(batches) == 3
    ledger = json.loads((tmp_path / "pkg" / "progress.json").read_text(encoding="utf-8"))
    assert all(entry["status"] == "pending" for entry in ledger)
    for batch in batches:
        content = batch.read_text(encoding="utf-8")
        assert "不可信数据" in content and "判定规则" in content
        assert "rating" not in content
    archive = json.loads(
        (tmp_path / "pkg" / "noise.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert archive["rating"] == 1.0 and archive["text"]


def test_check_rejects_tampered_answers(tmp_path):
    store, _ = _store_fixture(tmp_path)
    pool = collect_unjudged_pool(store)
    items = {item["id"]: item for items in pool.values() for item in items}
    answers = [
        {"id": "noise:1", "polarity": "opposing", "reason": "normal", "why": "x", "span": "NOPE"},
        {"id": "noise:2", "polarity": "sideways", "reason": "normal", "why": "x", "span": ""},
        {"id": "noise:2", "polarity": "opposing", "reason": "normal", "why": "x", "span": ""},
        {"id": "noise:9", "polarity": "opposing", "reason": "normal", "why": "x", "span": ""},
        {"id": "noise:3", "polarity": "opposing", "reason": "normal", "why": "x" * 13, "span": ""},
        {"id": "leakage:4", "polarity": "opposing", "reason": "off_topic", "why": "x", "span": ""},
    ]
    problems = check(items, answers)
    assert any("noise:1: span not verbatim" in p for p in problems)
    assert any("noise:2: bad polarity" in p for p in problems)
    assert any("duplicate id noise:2" in p for p in problems)
    assert any("unknown id noise:9" in p for p in problems)
    assert any("noise:3: why too long" in p for p in problems)
    assert not any("leakage:4" in p for p in problems)


def _answer(answer_id, polarity, reason, span, why="x"):
    return {"id": answer_id, "polarity": polarity, "reason": reason, "span": span, "why": why}


def test_merge_answers_builds_master_rows_and_aggregate_only_summary(tmp_path):
    store, _ = _store_fixture(tmp_path)
    pool = collect_unjudged_pool(store)
    pool_files = []
    for aspect, items in pool.items():
        path = tmp_path / f"{aspect}.jsonl"
        path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
            encoding="utf-8",
        )
        pool_files.append(path)
    answers_dir = tmp_path / "answers"
    answers_dir.mkdir()
    records = [
        _answer("noise:1", "opposing", "normal", "noise"),
        _answer("noise:2", "insufficient", "off_topic", ""),
        _answer("noise:3", "supporting", "comparative", "noise"),
        _answer("leakage:4", "insufficient", "vague", "leak"),
    ]
    (answers_dir / "noise.answers.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records[:3]), encoding="utf-8"
    )
    (answers_dir / "leakage.answers.jsonl").write_text(
        json.dumps(records[3], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / "old.answers.jsonl").write_text(
        json.dumps(_answer("noise:999", "supporting", "normal", "noise"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rows, summary, problems = merge_answers(
        pool_files, [answers_dir, stale], labeled_by="test-labeler", labeled_date="2026-09-26"
    )
    assert problems == []
    assert summary["answers_outside_pool"] == 1
    assert len(rows) == 4
    assert rows[0]["labeled_by"] == "test-labeler"
    assert summary["labelled_total"] == 4
    assert summary["direction_total"] == 2
    assert summary["off_topic_total"] == 1
    assert summary["still_unclear_total"] == 1
    assert summary["insufficient_reasons"]["vague"] == 1
    assert summary["aspects"]["noise"]["star_agreement"] == 1.0
    assert summary["aspects"]["noise"]["direction_rate"] == round(2 / 3, 3)
    assert summary["contract_problem_count"] == 0
    assert "answer_sources" in summary and len(summary["answer_sources"]) == 3
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "noise without" not in serialized and "span" not in serialized


def test_star_proxy_and_relation_follow_the_campaign_contract():
    assert star_proxy(5) == "supporting" and star_proxy(1) == "opposing"
    assert star_proxy(3) == "neutral" and star_proxy(None) == "none"
    item = {"polarity": "opposing", "rating": 1.0}
    assert relation(item) == "agree"
    assert relation({"polarity": "supporting", "rating": 1.0}) == "contradict"
    assert relation({"polarity": "conflicting", "rating": 5.0}) == "conflicting"


def _run_cli(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True,
        text=True,
        cwd=SCRIPTS.parent,
        check=False,
    )


def test_cli_merge_exits_nonzero_on_contract_violation(tmp_path):
    store, _ = _store_fixture(tmp_path)
    pool = collect_unjudged_pool(store)
    pool_file = tmp_path / "pool.jsonl"
    pool_file.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for items in pool.values() for item in items),
        encoding="utf-8",
    )
    answers_dir = tmp_path / "answers"
    answers_dir.mkdir()
    (answers_dir / "a.answers.jsonl").write_text(
        json.dumps(_answer("noise:1", "opposing", "normal", "WRONG"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result = _run_cli(
        "merge_label_answers.py",
        "--pool",
        str(pool_file),
        "--answers",
        str(answers_dir),
        "--out",
        str(tmp_path / "master.jsonl"),
        "--labeled-by",
        "test-labeler",
    )
    assert result.returncode == 3
    payload = json.loads(result.stderr.strip().splitlines()[-1])
    assert payload["status"] == "CONTRACT_VIOLATIONS" and payload["count"] >= 1
    assert not (tmp_path / "master.jsonl").exists()


def test_cli_merge_writes_master_and_aggregate_report(tmp_path):
    store, _ = _store_fixture(tmp_path)
    pool = collect_unjudged_pool(store)
    pool_file = tmp_path / "pool.jsonl"
    pool_file.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for items in pool.values() for item in items),
        encoding="utf-8",
    )
    answers_dir = tmp_path / "answers"
    answers_dir.mkdir()
    (answers_dir / "a.answers.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in (
                _answer("noise:1", "opposing", "normal", "noise"),
                _answer("noise:2", "insufficient", "off_topic", ""),
                _answer("noise:3", "supporting", "comparative", "noise"),
                _answer("leakage:4", "insufficient", "vague", "leak"),
            )
        ),
        encoding="utf-8",
    )
    report = tmp_path / "audit.json"
    result = _run_cli(
        "merge_label_answers.py",
        "--pool",
        str(pool_file),
        "--answers",
        str(answers_dir),
        "--out",
        str(tmp_path / "master.jsonl"),
        "--report",
        str(report),
        "--labeled-by",
        "test-labeler",
    )
    assert result.returncode == 0, result.stderr
    master = (tmp_path / "master.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(master) == 4
    audit = json.loads(report.read_text(encoding="utf-8"))
    assert audit["schema_version"] == "label-backfill-audit-v1"
    assert audit["labelled_total"] == 4 and audit["contract_problem_count"] == 0
