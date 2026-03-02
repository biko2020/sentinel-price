# =============================================================================
#  SentinelPrice · Daily Crawl DAG
# =============================================================================
#  Orchestrates the full daily price monitoring pipeline:
#    1. Health-check the database
#    2. Run all retailer spiders in parallel task groups
#    3. Verify data was written successfully
#    4. Trigger downstream reporting (optional)
#
#  Schedule:  Daily at 08:00 UTC
#  Retries:   2 per task, 5-minute backoff
#
#  Usage:
#    Place this file in your Airflow DAGs folder.
#    Trigger manually:  airflow dags trigger sentinelprice_daily_crawl
# =============================================================================

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.task_group import TaskGroup
from airflow.utils.trigger_rule import TriggerRule


# -----------------------------------------------------------------------------
#  Default arguments — inherited by all tasks
# -----------------------------------------------------------------------------

DEFAULT_ARGS = {
    "owner":            "sentinelprice",
    "depends_on_past":  False,
    "email_on_failure": False,   # Use AlertPipeline instead
    "email_on_retry":   False,
    "retries":          2,
    "retry_delay":      timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

# Base docker-compose command — adjust path if your project is not at /opt/sentinelprice
COMPOSE_BASE = "cd /opt/sentinelprice && docker-compose run --rm scraper"
PSQL_BASE    = "cd /opt/sentinelprice && docker-compose exec -T db psql -U sentinel_user -d sentinelprice"


# =============================================================================
#  DAG Definition
# =============================================================================

with DAG(
    dag_id            = "sentinelprice_daily_crawl",
    description       = "Daily price monitoring crawl across all retailers",
    schedule_interval = "0 8 * * *",   # Every day at 08:00 UTC
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,             # Prevent overlapping crawl runs
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "crawl", "daily"],
) as dag:

    # -------------------------------------------------------------------------
    #  Start sentinel
    # -------------------------------------------------------------------------

    start = EmptyOperator(task_id="start")

    # -------------------------------------------------------------------------
    #  Health check — confirm DB is up before crawling
    # -------------------------------------------------------------------------

    db_health_check = BashOperator(
        task_id         = "db_health_check",
        bash_command    = f"{PSQL_BASE} -c 'SELECT 1;'",
        retries         = 3,
        retry_delay     = timedelta(seconds=30),
    )

    # -------------------------------------------------------------------------
    #  Crawl task group — all spiders run in parallel
    # -------------------------------------------------------------------------

    with TaskGroup("crawl", tooltip="Run all retailer spiders") as crawl_group:

        crawl_amazon = BashOperator(
            task_id      = "amazon",
            bash_command = f"{COMPOSE_BASE} scrapy crawl amazon_spider",
        )

        crawl_walmart = BashOperator(
            task_id      = "walmart",
            bash_command = f"{COMPOSE_BASE} scrapy crawl walmart_spider",
        )

        crawl_target = BashOperator(
            task_id      = "target",
            bash_command = f"{COMPOSE_BASE} scrapy crawl target_spider",
        )

        crawl_ebay = BashOperator(
            task_id      = "ebay",
            bash_command = f"{COMPOSE_BASE} scrapy crawl ebay_spider",
        )

        crawl_bestbuy = BashOperator(
            task_id      = "bestbuy",
            bash_command = f"{COMPOSE_BASE} scrapy crawl bestbuy_spider",
        )

    # -------------------------------------------------------------------------
    #  Verify — confirm rows were written in this run window
    # -------------------------------------------------------------------------

    verify_data = BashOperator(
        task_id      = "verify_data",
        bash_command = (
            f"{PSQL_BASE} -c \""
            "SELECT COUNT(*) AS snapshots_last_hour "
            "FROM pricing_history "
            "WHERE scraped_at >= NOW() - INTERVAL '1 hour';"
            "\""
        ),
        trigger_rule = TriggerRule.ALL_DONE,   # Run even if some spiders failed
    )

    # -------------------------------------------------------------------------
    #  Summary log — print latest prices to task logs
    # -------------------------------------------------------------------------

    log_summary = BashOperator(
        task_id      = "log_summary",
        bash_command = (
            f"{PSQL_BASE} -c \""
            "SELECT source, COUNT(*) AS products, MAX(scraped_at) AS last_seen "
            "FROM latest_prices "
            "GROUP BY source "
            "ORDER BY source;"
            "\""
        ),
    )

    # -------------------------------------------------------------------------
    #  End sentinel
    # -------------------------------------------------------------------------

    end = EmptyOperator(
        task_id      = "end",
        trigger_rule = TriggerRule.ALL_DONE,
    )

    # -------------------------------------------------------------------------
    #  Task dependencies
    # -------------------------------------------------------------------------

    start >> db_health_check >> crawl_group >> verify_data >> log_summary >> end