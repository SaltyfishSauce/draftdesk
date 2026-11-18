FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[anthropic]"

# Run as an unprivileged user; /app/data holds the SQLite database.
RUN useradd --create-home draftdesk && mkdir -p /app/data && chown -R draftdesk /app
USER draftdesk

EXPOSE 8000
CMD ["draftdesk", "serve", "--host", "0.0.0.0", "--port", "8000", "-c", "config/config.yaml"]
