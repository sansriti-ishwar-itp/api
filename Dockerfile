FROM python:3.11-slim

WORKDIR /app

COPY . /app/

RUN python -m pip install --no-cache-dir --upgrade pip \
  && python -m pip install --no-cache-dir ".[dev]"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

