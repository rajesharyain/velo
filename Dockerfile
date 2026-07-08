FROM python:3.11-slim

# FFmpeg from apt — stays inside the image, no host install needed
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "velo_web:app", "--host", "0.0.0.0", "--port", "8000"]
