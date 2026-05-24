FROM python:3.14-slim

WORKDIR /app

COPY gateway/ /app/gateway/
COPY schema/ /app/schema/
COPY analyzers/ /app/analyzers/
COPY gateway/requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/gateway

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8900"]
