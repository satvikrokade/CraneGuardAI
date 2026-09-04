FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for OpenCV and sounddevice
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port
EXPOSE 7860

# Start server
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
