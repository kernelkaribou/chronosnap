# ChronoSnap

A self-hosted timelapse management application that automates the capture, organization, and video creation of timelapse projects. Configure a capture source, set a schedule, and let it run -- whether that is a few hours or an entire year.

ChronoSnap runs as a single Docker container with a built-in web interface. No external services, no cloud dependencies, no accounts. Everything stays on your hardware.

### What can you do with it?

- Point it at a security camera and take a daily photo for a year to watch the seasons change
- Attach a USB webcam to a Raspberry Pi and capture a garden growing from seed to harvest
- Pull hourly weather radar images from an HTTP endpoint and compile them into storm progression videos
- Automate a 3D print timelapse by capturing at regular intervals during a print job
- Import a batch of photos you already took and turn them into a timelapse video
- ...and more

---

## Features

### Capture and Scheduling

- Schedule captures at any interval from seconds to hours
- Set a time window within each day (e.g., only capture between 8:00 AM and 6:00 PM)
- Define start and end dates for the full capture period
- Supports RTSP/RTSPS streams, HTTP/HTTPS image endpoints, and locally attached cameras
- Adjustable capture quality and resolution per job
- Configurable file naming patterns with variables for job name, count, and timestamp

### Video Building

- Build timelapse videos from any range of captures within a job
- Adjustable resolution, framerate (1--120 FPS), and quality levels
- Text overlay support with customizable font, size, color, position, and opacity
- Dynamic overlay variables including job name, date, time, frame count, and more
- Live preview of text overlay before building
- Background processing with real-time progress tracking
- Automatic video thumbnail generation

### Automated Builds

- Enable per-job auto-build on a configurable interval (hourly to yearly)
- Automatically compiles new captures since the last build into a video
- Configurable FPS, quality, resolution, and text overlay for auto-built videos
- Webhook notifications on build completion

### Import and Export

- Upload images, videos, or archives directly through the browser (drag and drop or file/folder picker)
- Import from a server-mounted directory path
- Archive support for ZIP, TAR, GZ, RAR, and 7Z formats with automatic extraction
- Export jobs as ZIP archives containing all captures, videos, and job metadata
- Re-import exported archives with automatic detection of job configuration, tags, and naming
- Stream credentials are redacted from exports for safe sharing
- Video duplicate detection prevents redundant imports

### Organization and Review

- Tag jobs and videos with custom labels and colors
- Favorite individual captures and videos for quick access
- Compare captures side-by-side or with an interactive slider overlay
- Filter and sort captures by date, favorites, or tags
- Full-screen image viewer with navigation
- Responsive layout that works on desktop, tablet, and mobile

### Sharing

- Generate shareable links for completed videos (no authentication required for viewers)
- Toggle sharing on or off per video
- Shared links include security headers and content restrictions

### Storage and Monitoring

- Storage dashboard showing per-job breakdowns of capture and video usage
- Disk usage summary with total, used, and available space
- Event log tracking job activity, video builds, imports, and system events
- Webhook integration for alerts on warnings, completions, recoveries, and auto-builds
- Customizable webhook payload templates with variable substitution (compatible with Home Assistant and other automation platforms)

### Interface

- Dark and light mode with five visual themes (Cosmic, Ocean, Forest, Sunset, Minimal)
- Mobile-friendly responsive design
- Built-in API documentation (Swagger UI) at `/docs`
- Version check with update notifications

---

## Installation

ChronoSnap runs as a Docker container. The only requirement is a host with Docker and Docker Compose installed.

### Quick Start

1. Download the `docker-compose.yml` from this repository:

   [docker-compose.yml](https://raw.githubusercontent.com/kernelkaribou/timelapse-manager/main/docker-compose.yml)

2. Create the data directories:

   ```bash
   mkdir -p captures timelapses data
   ```

3. Start the container:

   ```bash
   docker compose up -d
   ```

4. Open `http://your-host:8080` in a browser.

That is all you need to get started. The application will initialize its database on first run and generate an API key for external access.

### Docker Compose Configuration

```yaml
services:
  timelapse-manager:
    image: ghcr.io/kernelkaribou/timelapse-manager:latest
    container_name: timelapse-manager
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/Chicago
      - LOG_LEVEL=INFO
    ports:
      - "8080:8080"
    volumes:
      - ./captures:/captures
      - ./timelapses:/timelapses
      - ./data:/app/data
      # - ./imports:/imports  # Optional: enable server-path imports
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - SETUID
      - SETGID
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

### Volumes

| Path | Purpose | Required |
|------|---------|----------|
| `/captures` | Stored capture images, organized by job | Yes |
| `/timelapses` | Built timelapse videos and thumbnails | Yes |
| `/app/data` | SQLite database and application settings | Yes |
| `/imports` | Server-side directory for bulk imports | No |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `0` | User ID for file ownership. Set to match your host user. |
| `PGID` | `0` | Group ID for file ownership. Set to match your host user. |
| `TZ` | `Etc/UTC` | Timezone for scheduling and timestamps. Use a valid tz identifier. |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `PORT` | `8080` | Port the application listens on inside the container. |
| `FFMPEG_TIMEOUT` | `10` | Timeout in seconds for ffmpeg frame capture operations. |

### Resource Limits

The included compose file sets resource limits of 2 CPU cores and 2 GB of RAM. These are reasonable defaults -- video encoding is the most resource-intensive operation. Adjust based on your hardware and how frequently you build videos.

---

## Capture Sources

### Network Streams (RTSP / HTTP)

Network sources are the most common setup. Point ChronoSnap at any camera or image endpoint accessible over your network.

**RTSP / RTSPS** -- Used by most security cameras and NVR systems. Provide the full stream URL from your camera's configuration.

```
rtsp://192.168.1.100:554/stream1
rtsps://10.0.10.1:7441/your-stream-token
```

**HTTP / HTTPS** -- Used for image endpoints, webcam snapshots, weather maps, or any URL that returns an image.

```
http://192.168.1.50:8080/snapshot.jpg
https://radar.weather.gov/ridge/standard/CONUS_loop.gif
```

When creating a job, use the "Test URL" button to verify the source is reachable and see a preview of what will be captured.

### Local Cameras

Local cameras (USB webcams, Raspberry Pi camera modules) require the device to be passed through to the Docker container.

**Step 1: Identify the device on your host**

```bash
# List video devices
ls /dev/video*

# Get device details (requires v4l-utils)
v4l2-ctl --list-devices
```

Cameras often register multiple `/dev/video` entries. You want the one associated with "Video Capture" -- typically the lowest-numbered device for that camera.

**Step 2: Pass the device into Docker**

Add the device to your `docker-compose.yml`:

```yaml
services:
  timelapse-manager:
    # ... existing config ...
    devices:
      - /dev/video0:/dev/video0
    # If using a Raspberry Pi camera module, also mount:
    # - /dev/vchiq:/dev/vchiq
```

For Raspberry Pi CSI cameras using `libcamera`, additional library mounts may be needed. Refer to your Pi's libcamera documentation for the shared library paths.

**Step 3: Create the job**

In the web interface, select "Local Device" as the source type. Available devices will be listed automatically. Select the device and test the capture before saving.

---

## Scheduling Guides

### Short-Term Capture (Hours to Days)

Ideal for 3D prints, construction progress within a day, or weather events.

- **Interval:** 5--30 seconds
- **Time window:** Disabled (capture continuously)
- **Example:** Capture every 10 seconds for 8 hours to record a 3D print. At 30 FPS, that produces roughly 96 seconds of video.

### Long-Term Capture (Weeks to Months)

Ideal for garden growth, construction projects, or seasonal changes.

- **Interval:** 5--60 minutes
- **Time window:** Optional, but useful to capture at consistent lighting
- **Example:** Capture every 15 minutes from sunrise to sunset for 3 months. Auto-build weekly videos to track progress without manual intervention.

### Daily Snapshot Over Time (Months to Years)

Ideal for yearly comparisons, landscape changes, or long-duration monitoring.

- **Interval:** 60 seconds (minimum, used with a time window)
- **Time window:** Set start and end to the same time (e.g., 12:00 to 12:00) to capture once per day at that time
- **Example:** One photo at noon every day for a year. 365 frames at 10 FPS gives a 36-second video showing the full year.

### Tips

- For outdoor captures, using a time window avoids dark nighttime frames that add noise to the video.
- Lower intervals generate more data. A 5-second interval at 1080p can produce several GB per day.
- Auto-build is useful for long-running jobs so you can review progress without manually building videos.
- Set the `TZ` environment variable to match your local timezone so scheduling and timestamps are intuitive.

---

## Technical Overview

### Architecture

ChronoSnap is a single-container application with three layers:

- **Backend:** Python with FastAPI, handling scheduling, capture, video processing, and the REST API
- **Frontend:** Vanilla JavaScript served as static files through the same container -- no build toolchain or framework dependencies
- **Database:** SQLite with WAL journaling for safe concurrent access from the scheduler, API, and video processing threads

The scheduler runs as a background thread, managing capture timing for all active jobs. Video builds are processed by spawning ffmpeg as a subprocess with real-time progress tracking. There are no external service dependencies -- everything runs within the single container.

### Data Storage

All captured images are stored on disk in a hierarchical directory structure organized by job, year, month, day, and hour. File paths are stored as relative references in the database, making the capture directory portable. Built videos and their thumbnails are stored in the timelapses volume with a similar per-job structure.

The SQLite database holds job configuration, capture metadata, video records, tags, shared links, and application settings. It is stored in the `/app/data` volume.

**Storage recommendation:** Keep the database on local storage (SSD preferred). SQLite relies on filesystem locking, which does not work reliably over network mounts (NFS, SMB). Capture and video volumes can be on network storage if needed, though local storage will provide better performance during video builds.

### Security and Privacy

ChronoSnap is designed for self-hosted use. No data leaves your network unless you explicitly configure webhooks or share a video link.

**Authentication:**
- All API endpoints require an API key (auto-generated, visible in Settings)
- The web interface is exempt from key requirements when accessed from the same origin
- Shared video links are the only public-facing routes

**Network hardening:**
- Security response headers on all requests (content type enforcement, frame embedding prevention, referrer restrictions, permission restrictions)
- CORS restricted to same-origin only
- Path traversal protections on all file-serving endpoints
- Parameterized database queries throughout

**Container hardening:**
- All Linux capabilities dropped by default, with only file ownership capabilities added back
- Privilege escalation prevention enabled
- Configurable resource limits for CPU and memory
- Health check endpoint for container orchestration

**Data privacy:**
- Stream URLs (which may contain camera credentials) are redacted from all exports
- Shared video links serve only the video file with restricted content security policies
- No telemetry, analytics, or external calls (aside from an optional GitHub version check)

### API

The full API is documented interactively at `/docs` (Swagger UI) when the application is running. All endpoints are under `/api/` and require authentication via the `X-API-Key` header or `api_key` query parameter.

**Example: Create a capture job**

```bash
curl -X POST http://localhost:8080/api/jobs/ \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Front Yard",
    "url": "rtsp://192.168.1.100:554/stream",
    "stream_type": "rtsp",
    "interval_seconds": 300,
    "start_datetime": "2026-01-01T08:00:00",
    "end_datetime": "2026-12-31T20:00:00",
    "time_window_enabled": true,
    "time_window_start": "08:00",
    "time_window_end": "18:00"
  }'
```

**Example: Build a video from captures**

```bash
curl -X POST http://localhost:8080/api/videos/ \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": 1,
    "name": "January Timelapse",
    "framerate": 30,
    "quality": "high",
    "resolution": "1920x1080",
    "start_time": "2026-01-01T00:00:00",
    "end_time": "2026-01-31T23:59:59"
  }'
```

**Example: List all jobs and their status**

```bash
curl http://localhost:8080/api/jobs/ \
  -H "X-API-Key: YOUR_KEY"
```

---

## Considerations

### Timezone Configuration

Set the `TZ` environment variable to your local timezone. Scheduling, time windows, and capture timestamps all rely on this. Using UTC (the default) works but may make time-window scheduling less intuitive.

### Storage Planning

Capture frequency and image resolution are the primary drivers of storage usage. Rough estimates for 1080p JPEG captures:

- Every 60 seconds: ~1.5 GB/day
- Every 5 minutes: ~300 MB/day
- Once per day: ~2 MB/day

### Backup

Back up the `/app/data` directory (contains the database) and the `/captures` volume. The `/timelapses` volume can be recreated from captures if needed. Export archives are a convenient way to create portable backups of individual jobs.

### Job Deletion

Deleting a job removes all its captures, videos, thumbnails, and database records. This is irreversible. Export the job first if you may want the data later.

### Network and Camera Reliability

Cameras and network streams can be intermittent. ChronoSnap tracks consecutive failures per job and sends webhook notifications when the warning threshold is reached. When a job recovers, a recovery notification is sent. The capture scheduler continues retrying on the configured interval.

---

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Frontend | Vanilla JavaScript, Alpine.js (minimal), HTML/CSS |
| Database | SQLite with WAL journaling |
| Video Processing | ffmpeg |
| Container | Docker (Python slim base image) |
| Authentication | API key (stateless, per-request validation) |

## License

MIT License. See [LICENSE](LICENSE) for details.

## Author

Maintained by [kernelkaribou](https://github.com/kernelkaribou)

## Disclaimer

This was a shell script I had personally made before but wanted something easier to configure and schedule on a larger scale. This application was built almost exclusively using vibe coding. If you are reading this you should know because some people get upset. This was an idea I carried around for a while and AI made it actually happen. I can't imagine that I will be making much changes to it as its more of a utility than an application but intend to keep it functioning as long as I can. Do whatever you want with it, I don't care.
