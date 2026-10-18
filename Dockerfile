FROM python:3.12-slim

# 1 — поставить Chromium для адаптера `playwright` (образ станет больше примерно на 500 МБ)
ARG WITH_PLAYWRIGHT=0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install . \
 && if [ "$WITH_PLAYWRIGHT" = "1" ]; then pip install "playwright>=1.45" && playwright install --with-deps chromium; fi

COPY alembic.ini ./
COPY alembic ./alembic
COPY sources ./sources

RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
