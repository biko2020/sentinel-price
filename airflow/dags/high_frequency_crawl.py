# =============================================================================
#  SentinelPrice · High-Frequency Crawl DAG
# =============================================================================
#  Schedule:  Every 4 hours — Amazon and eBay only
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
    "retries":           1,
    "retry_delay":       timedelta(minutes=2),
    "execution_timeout": timedelta(minutes=15),
}

SCRAPER_IMAGE = "sentinel-price-scraper"
DOCKER_RUN = (
    "docker run --rm"
    " --network sentinel-price_sentinel_net"
    " --env-file /opt/sentinelprice/.env"
    " -e POSTGRES_HOST=db"
    " " + SCRAPER_IMAGE
)

with DAG(
    dag_id            = "sentinelprice_high_frequency_crawl",
    description       = "High-frequency crawl for volatile retailers (Amazon, eBay)",
    schedule_interval = "0 */4 * * *",
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "crawl", "high-frequency"],
) as dag:

    start = EmptyOperator(task_id="start")

    with TaskGroup("crawl") as crawl_group:
        BashOperator(task_id="amazon", bash_command=DOCKER_RUN + " scrapy crawl amazon_spider")
        BashOperator(task_id="ebay",   bash_command=DOCKER_RUN + " scrapy crawl ebay_spider")

    end = EmptyOperator(task_id="end", trigger_rule=TriggerRule.ALL_DONE)

    start >> crawl_group >> end