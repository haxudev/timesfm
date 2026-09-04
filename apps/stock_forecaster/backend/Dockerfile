FROM python:3.11-slim

WORKDIR /app
COPY . /repo
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu torch \
  && pip install --no-cache-dir -e "/repo[torch]" \
  && pip install --no-cache-dir -e /repo/apps/stock_forecaster/backend

EXPOSE 8000
CMD ["uvicorn", "stock_forecaster.main:app", "--host", "0.0.0.0", "--port", "8000"]
