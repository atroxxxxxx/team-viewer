FROM python:3.12-slim-bookworm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ticket_service ./ticket_service
COPY openapi.json .
USER nobody
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "ticket_service"]
