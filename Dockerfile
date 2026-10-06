FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py template_engine.py patched_bot.py .
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
CMD ["python", "patched_bot.py"]
