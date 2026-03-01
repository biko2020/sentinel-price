
# =============================================================================
#  SentinelPrice · API Database
# =============================================================================
#  Async PostgreSQL connection using the `databases` library.
#  Shares the same DB credentials as the Scrapy pipeline via .env.
# =============================================================================

import os
from databases import Database
from dotenv import load_dotenv

load_dotenv()

DB_URL = (
    "postgresql://"
    f"{os.environ.get('POSTGRES_USER', 'sentinel_user')}:"
    f"{os.environ.get('POSTGRES_PASSWORD', '')}@"
    f"{os.environ.get('POSTGRES_HOST', 'db')}:"
    f"{os.environ.get('POSTGRES_PORT', '5432')}/"
    f"{os.environ.get('POSTGRES_DB', 'sentinelprice')}"
)

database = Database(DB_URL)