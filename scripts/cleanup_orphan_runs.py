"""One-off cleanup for runs #002-#005: duplicate reconciliation runs created by
repeated "Start run" clicks before the single-open-run invariant existed. They
were never reviewed and carry no audit value, so they are deleted rather than
force-closed (force-closing would misrepresent them as reviewed).

Usage: dotenv -e .env -- python3 scripts/cleanup_orphan_runs.py
"""

import os

from sqlalchemy import create_engine, text

ORPHAN_RUN_IDS = (2, 3, 4, 5)


def main():
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT id, status FROM reconciliation_runs ORDER BY id")
        ).all()
        print("Before:", before)

        unsafe = conn.execute(
            text(
                "SELECT DISTINCT r.id, r.status "
                "FROM reconciliation_runs r "
                "LEFT JOIN reconciliation_items i ON i.run_id = r.id "
                "WHERE r.id = ANY(:ids) "
                "AND (r.status = 'CLOSED' OR i.review_status NOT IN "
                "('PENDING', 'NOT_REQUIRED'))"
            ),
            {"ids": list(ORPHAN_RUN_IDS)},
        ).all()
        if unsafe:
            raise RuntimeError(f"refusing to delete reviewed or closed runs: {unsafe}")

        conn.execute(
            text(
                "DELETE FROM field_differences WHERE item_id IN "
                "(SELECT id FROM reconciliation_items WHERE run_id = ANY(:ids))"
            ),
            {"ids": list(ORPHAN_RUN_IDS)},
        )
        conn.execute(
            text("DELETE FROM reconciliation_items WHERE run_id = ANY(:ids)"),
            {"ids": list(ORPHAN_RUN_IDS)},
        )
        conn.execute(
            text(
                "DELETE FROM audit_events WHERE entity_type = 'reconciliation_run' "
                "AND entity_id = ANY(:ids)"
            ),
            {"ids": [str(i) for i in ORPHAN_RUN_IDS]},
        )
        conn.execute(
            text("DELETE FROM reconciliation_runs WHERE id = ANY(:ids)"),
            {"ids": list(ORPHAN_RUN_IDS)},
        )

        after = conn.execute(
            text("SELECT id, status FROM reconciliation_runs ORDER BY id")
        ).all()
        print("After:", after)


if __name__ == "__main__":
    main()
