FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/home/app/.cache/huggingface \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2

RUN apt-get update \
  && apt-get install -y --no-install-recommends libgomp1 \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 10001 app \
  && mkdir -p /data /run/research /home/app/.cache/huggingface \
  && chown -R app:app /data /run/research /home/app

WORKDIR /app
COPY . /repo
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu torch \
  && pip install --no-cache-dir -e "/repo[torch]" \
  && pip install --no-cache-dir -e "/repo/apps/stock_forecaster/backend[dev]"

RUN find /repo -type f -name '*.py' -exec chmod 0644 {} +

USER app
EXPOSE 8000
CMD ["uvicorn", "stock_forecaster.main:app", "--host", "0.0.0.0", "--port", "8000"]
