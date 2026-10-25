FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements-studio.txt ./
RUN pip install --no-cache-dir -r requirements-studio.txt
COPY studio ./studio
COPY outputs/character-focus-reference.png ./outputs/character-focus-reference.png
COPY outputs/the-empty-chair-drawing.mp4 outputs/the-empty-chair-drawing.srt ./outputs/
COPY outputs/the-empty-chair-source/assets ./outputs/the-empty-chair-source/assets
RUN useradd --create-home studio && mkdir /data && chown studio:studio /data
USER studio
ENV APP_ENV=production STUDIO_DATA_DIR=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "studio.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
