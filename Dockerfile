FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY config.yaml ./config.yaml

RUN pip install --no-cache-dir .

# Alert-only image: outbound HTTPS, no wallet, no shell entrypoint beyond the CLI.
ENV CONFIG_PATH=/app/config.yaml \
    STATE_PATH=/data/monitor_state.json \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]

ENTRYPOINT ["depeg-monitor"]
CMD ["--loop", "--interval", "30"]
