import sys
import time

from sqlalchemy.engine import Engine


def load_concepts_from_mysql(engine: Engine) -> list[dict]:
    start_time = time.time()

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor(dictionary=True)
        db_start = time.time()
        cursor.execute(
            "SELECT DISTINCT concept_id,concept_name FROM latest_distributions"
        )
        concepts = cursor.fetchall()
        db_time = time.time() - db_start
    finally:
        raw_conn.close()

    total_time = time.time() - start_time
    concepts_size = sys.getsizeof(concepts)

    print("\n[Profiling] load_concepts_from_mysql")
    print(f"  - DB time (query + fetch): {db_time:.2f}s")
    print(f"  - Total time: {total_time:.2f}s")
    print(f"  - Concepts loaded: {len(concepts)}")
    print(f"  - Estimated memory: {concepts_size / 1024 / 1024:.2f}MB\n")

    return concepts
