# =============================================================================
#  SentinelPrice · High-Frequency Crawl DAG
# =============================================================================
#  Crawls a subset of high-priority retailers more frequently for
#  time-sensitive price monitoring (flash sales, deal windows).
#
#  Schedule:  Every 4 hours
#  Retailers: Amazon and eBay only (most volatile pricing)
#  Retries:   1 per task, 2-minute backoff
#
#  To add a retailer to high-frequency monitoring:
#    Add a BashOperator task inside the crawl TaskGroup below.
#
#  Usage:
#    airflow dags trigger sentinelprice_high_frequency_crawl
# =============================================================================

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.task_group import TaskGroup
from airflow.utils.trigger_rule import TriggerRule


DEFAULT_ARGS = {
    "owner":             "sentinelprice",
    "depends_on_past":   False,
    "email_on_failure":  False,
    "email_on_retry":    False,
    "retries":           1,
    "retry_delay":       timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=15),
}

COMPOSE_BASE = "cd /opt/sentinelprice && docker-compose run --rm scraper"


with DAG(
    dag_id            = "sentinelprice_high_frequency_crawl",
    description       = "High-frequency crawl for volatile retailers (Amazon, eBay)",
    schedule_interval = "0 */4 * * *",   # Every 4 hours
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "crawl", "high-frequency"],
) as dag:

    start = EmptyOperator(task_id="start")

    with TaskGroup("crawl", tooltip="High-frequency retailer spiders") as crawl_group:

        crawl_amazon = BashOperator(
            task_id      = "amazon",
            bash_command = f"{COMPOSE_BASE} scrapy crawl amazon_spider",
        )

        crawl_ebay = BashOperator(
            task_id      = "ebay",
            bash_command = f"{COMPOSE_BASE} scrapy crawl ebay_spider",
        )

    end = EmptyOperator(
        task_id      = "end",
        trigger_rule = TriggerRule.ALL_DONE,
    )

    start >> crawl_group >> end