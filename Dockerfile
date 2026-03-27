FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build-time metadata — injected by the deploy workflow via --build-arg.
# Falls back to "unknown" for local/manual builds.
ARG GIT_SHA=unknown
ENV APP_GIT_SHA=$GIT_SHA

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
