# =============================================================================
#  SentinelPrice · Airflow Dockerfile
# =============================================================================
#  Extends the official Airflow image with:
#    · Docker CLI        — to execute docker commands from DAG BashOperators
#    · docker-compose    — to run scraper tasks via docker-compose
# =============================================================================

FROM apache/airflow:2.9.1-python3.11

USER root

# Install Docker CLI and docker-compose
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo \
       "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
       https://download.docker.com/linux/debian \
       $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        docker-ce-cli \
        docker-compose-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    # Symlink 'docker compose' plugin as 'docker-compose' for compatibility
    && ln -s /usr/libexec/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose

# Add airflow user to docker group so it can access the socket
RUN groupadd -f docker && usermod -aG docker airflow

# Install postgresql-client for direct DB access from DAG tasks
RUN apt-get update && apt-get install -y --no-install-recommends         postgresql-client     && apt-get clean     && rm -rf /var/lib/apt/lists/*

USER airflow