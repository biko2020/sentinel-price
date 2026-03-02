# =============================================================================
#  SentinelPrice · Weekly Maintenance DAG
# =============================================================================
#  Performs scheduled database hygiene tasks:
#    1. Archive old pricing snapshots (>90 days) to a cold table
#    2. Vacuum and analyze the pricing_history table
#    3. Refresh materialized views (if any)
#    4. Emit a weekly coverage report to logs
#
#  Schedule:  Every Sunday at 02:00 UTC
#  Retries:   1 per task
#
#  Usage:
#    airflow dags trigger sentinelprice_weekly_maintenance
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

PSQL_BASE = "cd /opt/sentinelprice && docker-compose exec -T db psql -U sentinel_user -d sentinelprice"


with DAG(
    dag_id            = "sentinelprice_weekly_maintenance",
    description       = "Weekly DB maintenance — archive, vacuum, coverage report",
    schedule_interval = "0 2 * * 0",   # Every Sunday at 02:00 UTC
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "maintenance", "weekly"],
) as dag:

    start = EmptyOperator(task_id="start")

    # -------------------------------------------------------------------------
    #  Archive snapshots older than 90 days into pricing_history_archive
    # -------------------------------------------------------------------------

    archive_old_snapshots = BashOperator(
        task_id      = "archive_old_snapshots",
        bash_command = (
            f"{PSQL_BASE} -c \""
            "CREATE TABLE IF NOT EXISTS pricing_history_archive "
            "  (LIKE pricing_history INCLUDING ALL); "
            "INSERT INTO pricing_history_archive "
            "  SELECT * FROM pricing_history "
            "  WHERE scraped_at < NOW() - INTERVAL '90 days' "
            "  ON CONFLICT DO NOTHING; "
            "DELETE FROM pricing_history "
            "  WHERE scraped_at < NOW() - INTERVAL '90 days';"
            "\""
        ),
    )

    # -------------------------------------------------------------------------
    #  Vacuum and analyze for query performance
    # -------------------------------------------------------------------------

    vacuum_analyze = BashOperator(
        task_id      = "vacuum_analyze",
        bash_command = (
            f"{PSQL_BASE} -c 'VACUUM ANALYZE pricing_history;' && "
            f"{PSQL_BASE} -c 'VACUUM ANALYZE products;'"
        ),
    )

    # -------------------------------------------------------------------------
    #  Weekly coverage report — logged to Airflow task output
    # -------------------------------------------------------------------------

    coverage_report = BashOperator(
        task_id      = "coverage_report",
        bash_command = (
            f"{PSQL_BASE} -c \""
            "SELECT "
            "  s.name                                   AS source, "
            "  COUNT(DISTINCT p.product_id)             AS tracked_products, "
            "  COUNT(ph.id)                             AS total_snapshots, "
            "  COUNT(ph.id) FILTER (WHERE ph.scraped_at >= NOW() - INTERVAL '7 days') "
            "                                           AS snapshots_this_week, "
            "  ROUND(AVG(ph.price_current)::numeric, 2) AS avg_price, "
            "  MAX(ph.scraped_at)                       AS last_crawl "
            "FROM sources s "
            "LEFT JOIN products p        ON p.source_id  = s.source_id "
            "LEFT JOIN pricing_history ph ON ph.product_id = p.product_id "
            "GROUP BY s.name "
            "ORDER BY s.name;"
            "\""
        ),
        trigger_rule = TriggerRule.ALL_DONE,
    )

    # -------------------------------------------------------------------------
    #  Row count sanity check
    # -------------------------------------------------------------------------

    row_count_check = BashOperator(
        task_id      = "row_count_check",
        bash_command = (
            f"{PSQL_BASE} -c \""
            "SELECT "
            "  (SELECT COUNT(*) FROM products)         AS total_products, "
            "  (SELECT COUNT(*) FROM pricing_history)  AS active_snapshots, "
            "  (SELECT COUNT(*) FROM pricing_history_archive) AS archived_snapshots;"
            "\""
        ),
    )

    end = EmptyOperator(
        task_id      = "end",
        trigger_rule = TriggerRule.ALL_DONE,
    )

    # -------------------------------------------------------------------------
    #  Task dependencies
    # -------------------------------------------------------------------------

    start >> archive_old_snapshots >> vacuum_analyze >> coverage_report >> row_count_check >> end