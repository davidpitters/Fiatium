FROM python:3.12.14-slim-bookworm AS dependencies
WORKDIR /build
COPY requirements.lock .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock

FROM python:3.12.14-slim-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates unixodbc \
    && curl -fsSLo /tmp/microsoft.deb https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i /tmp/microsoft.deb && rm /tmp/microsoft.deb \
    && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 fiatium
WORKDIR /app
COPY --from=dependencies /wheels /wheels
COPY requirements.lock .
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.lock && rm -rf /wheels
COPY services ./services
COPY db ./db
COPY alembic.ini .
ENV PYTHONPATH=/app/services PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER fiatium
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "fiatium.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS verification
USER root
COPY requirements-dev.lock .
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY tests ./tests
COPY scripts/smoke.py ./scripts/smoke.py
COPY pyproject.toml .
USER fiatium
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
