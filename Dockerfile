FROM python:3.11-slim

# Install ffmpeg, tzdata, gosu, curl, and archive tools
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    tzdata \
    gosu \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    fonts-liberation \
    p7zip-full \
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
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY entrypoint.sh /entrypoint.sh

# Make entrypoint executable
RUN chmod +x /entrypoint.sh

# Create necessary directories with secure permissions
RUN mkdir -p /app/data /captures /timelapses /imports /exports && \
    chmod 755 /app/data /captures /timelapses /imports /exports

# Add build metadata
LABEL org.opencontainers.image.title="Timelapse Manager" \
      org.opencontainers.image.description="Configuration and management tool for timelapse videos" \
      org.opencontainers.image.vendor="kernelkaribou" \
      org.opencontainers.image.source="https://github.com/kernelkaribou/timelapse-manager"

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=Etc/UTC
ENV PORT=8080
ENV LOG_LEVEL=INFO
ENV FFMPEG_TIMEOUT=10
ENV MAX_UPLOAD_SIZE=10737418240

# Expose port (can be overridden)
EXPOSE ${PORT}

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Run the application
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
