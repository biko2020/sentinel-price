# =============================================================================
#  SentinelPrice · Weekly Maintenance DAG
# =============================================================================
#  Schedule:  Every Sunday at 02:00 UTC
# =============================================================================

from __future__ import annotations
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.trigger_rule import TriggerRule

DEFAULT_ARGS = {
    "owner":             "sentinelprice",
    "depends_on_past":   False,
    "email_on_failure":  False,
    "retries":           1,
    "retry_delay":       timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=60),
}

PSQL = "psql postgresql://sentinel_user:$POSTGRES_PASSWORD@db:5432/sentinelprice"

with DAG(
    dag_id            = "sentinelprice_weekly_maintenance",
    description       = "Weekly DB maintenance — archive, vacuum, coverage report",
    schedule_interval = "0 2 * * 0",
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "maintenance", "weekly"],
) as dag:

    start = EmptyOperator(task_id="start")

    archive_old_snapshots = BashOperator(
        task_id      = "archive_old_snapshots",
        bash_command = (
            PSQL + ' -c "CREATE TABLE IF NOT EXISTS pricing_history_archive (LIKE pricing_history INCLUDING ALL);"'
            " && " + PSQL + ' -c "INSERT INTO pricing_history_archive SELECT * FROM pricing_history WHERE scraped_at < NOW() - INTERVAL \'90 days\' ON CONFLICT DO NOTHING;"'
            " && " + PSQL + ' -c "DELETE FROM pricing_history WHERE scraped_at < NOW() - INTERVAL \'90 days\';"'
        ),
    )

    vacuum_analyze = BashOperator(
        task_id      = "vacuum_analyze",
        bash_command = (
            PSQL + ' -c "VACUUM ANALYZE pricing_history;"'
            " && " + PSQL + ' -c "VACUUM ANALYZE products;"'
        ),
    )

    coverage_report = BashOperator(
        task_id      = "coverage_report",
        bash_command = (
            PSQL + ' -c "SELECT s.name AS source, COUNT(DISTINCT p.product_id) AS tracked_products, COUNT(ph.id) AS total_snapshots, MAX(ph.scraped_at) AS last_crawl FROM sources s LEFT JOIN products p ON p.source_id = s.source_id LEFT JOIN pricing_history ph ON ph.product_id = p.product_id GROUP BY s.name ORDER BY s.name;"'
        ),
        trigger_rule = TriggerRule.ALL_DONE,
    )

    row_count_check = BashOperator(
        task_id      = "row_count_check",
        bash_command = (
            PSQL + ' -c "SELECT (SELECT COUNT(*) FROM products) AS total_products, (SELECT COUNT(*) FROM pricing_history) AS active_snapshots;"'
        ),
    )

    end = EmptyOperator(task_id="end", trigger_rule=TriggerRule.ALL_DONE)

    start >> archive_old_snapshots >> vacuum_analyze >> coverage_report >> row_count_check >> end