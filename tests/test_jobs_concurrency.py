"""Two-thread claim race — every row claimed exactly once.

The §7.5 concurrency story rests on ``BEGIN IMMEDIATE`` + ``UPDATE …
WHERE status='pending' RETURNING *`` letting only one writer at a
time mutate the table. This test pounds on that primitive: two
threads call ``claim_next`` against the same DB; we assert no row is
double-claimed and no row is lost.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from engine.jobs import claim_next, connect, count_by_status, enqueue, init_db


def test_two_threads_each_row_claimed_once(tmp_path: Path) -> None:
    db = tmp_path / "jobs.db"
    init_db(db)

    n_jobs = 50
    conn = connect(db)
    try:
        ids = [enqueue(conn, "_mock_flaky", {"i": i}) for i in range(n_jobs)]
    finally:
        conn.close()

    import time as _time

    def drain(label: str) -> list[str]:
        """Each thread opens its own connection and claims until empty.

        A 1ms sleep between claims gives the other thread a real chance
        to interleave; without it, the GIL hands the loop to one
        thread and it drains everything before the other wakes up.
        That would technically pass "no duplicates" but wouldn't
        actually exercise the lock.
        """
        own_conn = connect(db)
        claimed: list[str] = []
        try:
            while True:
                job = claim_next(own_conn)
                if job is None:
                    break
                claimed.append(job.id)
                _time.sleep(0.001)
        finally:
            own_conn.close()
        return claimed

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = [ex.submit(drain, "A"), ex.submit(drain, "B")]
        results = [f.result() for f in as_completed(futures)]

    all_claims = [c for r in results for c in r]

    # The load-bearing invariant: every job claimed exactly once.
    # No duplicates (the lock works) and no losses (no row was missed).
    assert sorted(all_claims) == sorted(ids)
    assert len(set(all_claims)) == n_jobs

    conn = connect(db)
    try:
        counts = count_by_status(conn)
    finally:
        conn.close()
    # Every claim left the row in 'running' (no terminal transition in this test).
    assert counts.get("running") == n_jobs


def test_serial_drain_baseline(tmp_path: Path) -> None:
    """Sanity: a single-threaded drain claims every row once.

    Provides a baseline so a regression in the concurrent case isn't
    masked by a broken claim_next.
    """
    db = tmp_path / "jobs.db"
    init_db(db)

    conn = connect(db)
    try:
        ids = [enqueue(conn, "_mock_flaky", {}) for _ in range(20)]
        claimed: list[str] = []
        while True:
            j = claim_next(conn)
            if j is None:
                break
            claimed.append(j.id)
    finally:
        conn.close()

    assert sorted(claimed) == sorted(ids)
