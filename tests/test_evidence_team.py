"""Tests for the D2 reading team: deterministic fan-in, per-card failure
isolation, and genuine concurrency proven with a barrier."""

from __future__ import annotations

import threading

from ecom_review_intelligence.evidence_team import (
    MAX_READING_WORKERS,
    ReadingFailure,
    ReadingTask,
    execute_reading_team,
    summarize_reading,
)


def _task(alias: str) -> ReadingTask:
    return ReadingTask(card_alias=alias, payload={"card_alias": alias})


def test_results_come_back_keyed_in_submission_order() -> None:
    tasks = [_task("E1"), _task("E2"), _task("E3")]
    outcomes = execute_reading_team(tasks, lambda task: f"read-{task.card_alias}")
    assert list(outcomes) == ["E1", "E2", "E3"]
    assert outcomes["E2"] == "read-E2"


def test_one_failing_reader_never_takes_down_the_team() -> None:
    def reader(task: ReadingTask) -> object:
        if task.card_alias == "E2":
            raise RuntimeError("provider timeout")
        return f"read-{task.card_alias}"

    outcomes = execute_reading_team([_task("E1"), _task("E2"), _task("E3")], reader)
    assert outcomes["E1"] == "read-E1"
    assert isinstance(outcomes["E2"], ReadingFailure)
    assert "provider timeout" in outcomes["E2"].reason
    assert outcomes["E3"] == "read-E3"


def test_readers_actually_run_in_parallel() -> None:
    # A sequential executor would trip the barrier (each reader waits for all
    # four to arrive); a parallel one releases it immediately.
    barrier = threading.Barrier(4, timeout=5)
    threads: set[int] = set()
    lock = threading.Lock()

    def reader(task: ReadingTask) -> object:
        with lock:
            threads.add(threading.get_ident())
        barrier.wait()
        return task.card_alias

    tasks = [_task(f"E{index}") for index in range(1, 5)]
    outcomes = execute_reading_team(tasks, reader)
    assert all(not isinstance(outcome, ReadingFailure) for outcome in outcomes.values())
    assert len(threads) == 4


def test_empty_team_returns_empty_and_worker_cap_holds() -> None:
    assert execute_reading_team([], lambda task: task) == {}
    assert MAX_READING_WORKERS == 5


def test_summary_counts_statuses_and_model_calls() -> None:
    summary = summarize_reading(
        {"E1": "READY", "E2": "READY", "E3": "VALIDATION_FAILED", "E4": "UNAVAILABLE"},
        {"E1": 1, "E2": 1, "E3": 1, "E4": 0},
    )
    assert summary == {
        "total": 4,
        "ready": 2,
        "validation_failed": 1,
        "unavailable": 1,
        "model_calls": 3,
    }
    assert summarize_reading({}, {})["total"] == 0
