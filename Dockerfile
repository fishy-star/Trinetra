FROM python:3.11-slim

# Tesseract with English and Hindi for notice OCR; ffmpeg for voice notes and videos.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-hin ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY data/registry data/registry
# The speech model (python tools/download_models.py) is mounted at /srv/models, or STT stays off.

ENV LLM_MODE=off STT_MODE=off AUDIO_MODEL=off DATA_DIR=/srv/data/runtime
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
