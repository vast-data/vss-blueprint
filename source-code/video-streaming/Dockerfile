# Match video-backend base (python:3.10-slim) so OpenSSL/glibc match the image that already
# runs on strict/FIPS Kubernetes workers — avoids streaming-only native TLS/OpenCV mismatches.
FROM python:3.10-slim

# yt-dlp YouTube extraction often needs a JS runtime (n/challenge scripts). Without Node/Deno,
# you see "No supported JavaScript runtime" and format extraction fails intermittently.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp
RUN pip install --no-cache-dir yt-dlp

# Copy application files from src directory
COPY src/ ./

# Create recordings directory
RUN mkdir -p recordings

# Make scripts executable
RUN chmod +x *.py

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Default command
CMD ["python", "web_service.py"]
