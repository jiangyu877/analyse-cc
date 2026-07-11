FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY database ./database
COPY scripts ./scripts
COPY serve.py run.py ./

RUN useradd --create-home --uid 10001 webapp && chown -R webapp:webapp /app
USER webapp

EXPOSE 5000
CMD ["python", "serve.py"]
