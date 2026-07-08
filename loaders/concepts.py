import sys
import time

from sqlalchemy.engine import Engine

from logging_config import get_logger

log = get_logger("loaders.concepts")


def load_concepts_from_mysql(engine: Engine) -> list[dict]:
    start_time = time.time()

    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor(dictionary=True)
        db_start = time.time()
        cursor.execute(
            "SELECT DISTINCT concept_id,concept_name,domain_id FROM latest_distributions"
        )
        concepts = cursor.fetchall()
        db_time = time.time() - db_start
    finally:
        raw_conn.close()

    total_time = time.time() - start_time
    concepts_size = sys.getsizeof(concepts)

    log.info(
        f"[warmup] load_concepts_from_mysql: DB {db_time:.2f}s, total {total_time:.2f}s, "
        f"{len(concepts)} concepts, ~{concepts_size / 1024 / 1024:.2f}MB"
    )

    return concepts
