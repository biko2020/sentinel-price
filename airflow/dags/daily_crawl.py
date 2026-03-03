# =============================================================================
#  SentinelPrice · Daily Crawl DAG
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
    "retries":           2,
    "retry_delay":       timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

# Direct psql — double-quoted SQL avoids all shell escaping issues
PSQL = "psql postgresql://sentinel_user:$POSTGRES_PASSWORD@db:5432/sentinelprice"

# docker run directly — no docker-compose exec needed
SCRAPER_IMAGE = "sentinel-price-scraper"
DOCKER_RUN = (
    "docker run --rm"
    " --network sentinel-price_sentinel_net"
    " --env-file /opt/sentinelprice/.env"
    " -e POSTGRES_HOST=db"
    " " + SCRAPER_IMAGE
)

with DAG(
    dag_id            = "sentinelprice_daily_crawl",
    description       = "Daily price monitoring crawl across all retailers",
    schedule_interval = "0 8 * * *",
    start_date        = datetime(2025, 1, 1),
    catchup           = False,
    max_active_runs   = 1,
    default_args      = DEFAULT_ARGS,
    tags              = ["sentinelprice", "crawl", "daily"],
) as dag:

    start = EmptyOperator(task_id="start")

    db_health_check = BashOperator(
        task_id      = "db_health_check",
        bash_command = PSQL + ' -c "SELECT 1;"',
        retries      = 3,
        retry_delay  = timedelta(seconds=30),
    )

    with TaskGroup("crawl", tooltip="Run all retailer spiders") as crawl_group:
        BashOperator(task_id="amazon",  bash_command=DOCKER_RUN + " scrapy crawl amazon_spider")
        BashOperator(task_id="walmart", bash_command=DOCKER_RUN + " scrapy crawl walmart_spider")
        BashOperator(task_id="target",  bash_command=DOCKER_RUN + " scrapy crawl target_spider")
        BashOperator(task_id="ebay",    bash_command=DOCKER_RUN + " scrapy crawl ebay_spider")
        BashOperator(task_id="bestbuy", bash_command=DOCKER_RUN + " scrapy crawl bestbuy_spider")

    verify_data = BashOperator(
        task_id      = "verify_data",
        bash_command = PSQL + ' -c "SELECT COUNT(*) AS snapshots_last_hour FROM pricing_history WHERE scraped_at >= NOW() - INTERVAL \'1 hour\';"',
        trigger_rule = TriggerRule.ALL_DONE,
    )

    log_summary = BashOperator(
        task_id      = "log_summary",
        bash_command = PSQL + ' -c "SELECT source, COUNT(*) AS products, MAX(scraped_at) AS last_seen FROM latest_prices GROUP BY source ORDER BY source;"',
    )

    end = EmptyOperator(task_id="end", trigger_rule=TriggerRule.ALL_DONE)

    start >> db_health_check >> crawl_group >> verify_data >> log_summary >> end