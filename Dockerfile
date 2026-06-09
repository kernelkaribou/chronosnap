FROM python:3.13-slim

# Install ffmpeg, tzdata, gosu, curl, and archive tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    v4l-utils \
    tzdata \
    gosu \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    fonts-liberation \
    unrar-free && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY VERSION ./VERSION
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY entrypoint.sh /entrypoint.sh

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Create necessary directories
RUN mkdir -p /app/data /captures /timelapses /imports /app/import-staging

# Add build metadata
LABEL org.opencontainers.image.title="ChronoSnap" \
      org.opencontainers.image.description="Configuration and management tool for timelapse videos" \
      org.opencontainers.image.vendor="kernelkaribou" \
      org.opencontainers.image.source="https://github.com/kernelkaribou/chronosnap" \
      org.opencontainers.image.version="3.0.0"

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV APP_VERSION=""
ENV TZ=Etc/UTC
ENV PORT=8080
ENV LOG_LEVEL=INFO
ENV FFMPEG_TIMEOUT=10

# Expose port (can be overridden)
EXPOSE ${PORT}

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Run the application
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
