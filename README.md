# TimeLapse-Manager

A self-hosted web application for creating automated timelapse captures from HTTP and RTSP video streams. Define a schedule, point it at a camera, and let it collect images over days, weeks, or months, then process them into timelapse videos.

## Features

### Job Scheduling

- Configure capture jobs with a start date, end date, and interval (minimum 10 seconds between captures)
- Optional daily time windows to restrict captures to specific hours (for example, noon to 1 PM each day, or 6 AM to 8 PM for daylight only)
- Time windows support crossing midnight (for example, 22:00 to 06:00)
- Capture timing uses grid-based arithmetic aligned to the job start time, so intervals remain consistent regardless of when the scheduler checks
- All scheduling is DST-aware through UTC intermediate calculations

### Stream Support

- RTSP and RTSPS streams captured via FFmpeg with TCP transport
- HTTP/HTTPS snapshot URLs captured via FFmpeg
- URL validation with a test capture before committing to a job
- Manual snapshot trigger for on-demand captures outside the schedule

### Video Processing

- Generate timelapse videos from captured images using FFmpeg
- Configurable resolution, framerate, and quality (low, medium, high, lossless)
- Filter captures by time range or capture ID range when building a video
- Real-time progress tracking during processing
- Videos are preserved even if the parent job is deleted

### Management

- Web interface with light and dark themes
- 12-hour and 24-hour time display toggle
- Per-job capture sync tool to reconcile database records with files on disk
- Orphaned capture detection across all jobs
- Bulk capture deletion
- Job duplication to quickly create similar configurations
- API key authentication for all endpoints
- Health check endpoint for container orchestration

### Storage Dashboard

- Visual breakdown of storage usage across all jobs
- Donut charts for captures vs. timelapses and disk usage
- Per-job horizontal bar chart showing capture and timelapse sizes
- Summary cards for total captures, timelapses, storage used, and disk free

### Webhook Notifications

- Configurable webhook alerts when a job fails consecutive captures
- Customizable failure threshold (default: 3 consecutive failures)
- JSON payload template with variable substitution for integration with Home Assistant, Discord, Slack, or any HTTP endpoint
- Available template variables: `{title}`, `{message}`, `{job_name}`, `{job_id}`, `{failure_count}`, `{error_message}`
- Test button to verify webhook configuration before relying on it
- Alerts fire once when the threshold is first reached (non-spamming)

## Quick Start

```bash
docker-compose up -d
```

The web interface is available at `http://<host>:8080`. On first launch, an API key is generated automatically and displayed in the Settings view.

## Docker Configuration

### Volumes

Three volumes are required for persistent data:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./captures` | `/captures` | Captured images organized by job |
| `./timelapses` | `/timelapses` | Processed timelapse videos |
| `./data` | `/app/data` | SQLite database |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `0` | User ID for file ownership. Set this to match your host user to avoid permission issues. |
| `PGID` | `0` | Group ID for file ownership. |
| `TZ` | `Etc/UTC` | Timezone for scheduling and display. Must be a valid IANA timezone (for example, `America/Chicago`). This controls when time windows activate and how timestamps are displayed. |
| `PORT` | `8080` | Port the application listens on inside the container. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `FFMPEG_TIMEOUT` | `10` | Timeout in seconds for individual FFmpeg capture operations. Increase this if your camera streams are slow to respond. |

### Example docker-compose.yml

```yaml
services:
  timelapse-manager:
    image: ghcr.io/kernelkaribou/timelapse-manager:latest
    container_name: timelapse-manager
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/Chicago
    ports:
      - "8080:8080"
    volumes:
      - ./captures:/captures
      - ./timelapses:/timelapses
      - ./data:/app/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## Job Configuration

When creating a job, the following options are available:

| Field | Required | Description |
|-------|----------|-------------|
| Name | Yes | Display name for the job. |
| URL | Yes | Stream address. Supports `http://`, `https://`, `rtsp://`, and `rtsps://` schemes. |
| Stream Type | Yes | Either `http` (snapshot URL) or `rtsp` (video stream). |
| Start Date/Time | Yes | When the job begins capturing. |
| End Date/Time | No | When the job stops capturing. If omitted, the job runs indefinitely until manually stopped. |
| Interval | Yes | Seconds between captures. Minimum 10 seconds. |
| Framerate | No | Default framerate for generated videos. Defaults to 30 fps. |
| Time Window | No | When enabled, restricts captures to a daily window defined by a start and end time in HH:MM format. |

### Job States

Jobs transition between four states based on their schedule and configuration:

- **Active**: The job is within its scheduled date range and, if a time window is configured, within the active window. Captures are being taken at the defined interval.
- **Sleeping**: The job is within its date range but outside its daily time window, or the start date has not arrived yet. No captures are taken.
- **Completed**: The end date has passed. The job and its captures remain available for video processing.
- **Disabled**: Manually paused by the user. Can be re-enabled at any time.

### Scheduling Behavior

Capture times are calculated on a fixed grid starting from the job's start datetime. For example, a job starting at 14:00 with a 60-second interval will always target 14:00, 14:01, 14:02, and so on, regardless of when captures actually execute or how long they take.

When a daily time window is enabled, captures only occur during the defined hours. The grid alignment is preserved across window boundaries, so a job does not drift over time.

Time windows that cross midnight are supported. A window from 22:00 to 06:00 means captures run from 10 PM through 6 AM the following morning.

## File Storage

### Captures

Images are stored in a hierarchical directory structure under each job's capture path:

```
/captures/{job_id}_{job_name}/
  2026/
    01/
      15/
        14/
          jobname_000001_20260115_140000.jpg
          jobname_000002_20260115_140100.jpg
        15/
          jobname_000003_20260115_150000.jpg
```

Each capture also has a thumbnail generated automatically.

### Videos

Processed timelapse videos are stored in `/timelapses/` as MP4 files. Videos maintain a reference to their source job but are not deleted when a job is removed.

### Database

The SQLite database at `/app/data/timelapse-manager.db` stores all job configurations, capture metadata, video records, and settings. Back up this file along with the capture and timelapse directories to preserve your data.

## API

All API endpoints require authentication via API key, provided as either:

- Header: `X-API-Key: <key>`
- Query parameter: `?api_key=<key>`

The API key is generated on first launch and can be viewed or regenerated in the Settings view.

Interactive API documentation is available at `/docs` (Swagger UI) when the application is running.

### Endpoint Overview

| Prefix | Purpose |
|--------|---------|
| `GET /health` | Health check (no authentication required) |
| `/api/jobs` | Create, list, update, and delete capture jobs. Trigger manual captures. Run capture sync scans. |
| `/api/captures` | List, filter, download, and delete captures. Detect and clean up orphaned files. |
| `/api/videos` | Create timelapse videos, track processing progress, download and delete videos. |
| `/api/settings` | View and regenerate the API key. Configure webhook notifications. |
| `/api/storage` | Storage statistics and disk usage. |

## Considerations

### Timezone Configuration

The `TZ` environment variable is critical for correct scheduling behavior. All capture grid calculations convert through UTC to handle daylight saving time transitions correctly. If the container timezone does not match your intended schedule, time windows and capture times will be offset.

Changing the timezone on a running instance will not retroactively adjust existing job schedules. Jobs calculate their next capture relative to their original start datetime, so the grid remains consistent.

### Storage Planning

Captured images are typically 50 KB to 500 KB each depending on the source resolution and scene complexity. A job capturing once per minute generates roughly 1,440 images per day. At 200 KB average, that is approximately 280 MB per day or about 8.5 GB per month.

Plan your storage volumes accordingly for long-running jobs. The capture sync tool can help identify discrepancies between the database and filesystem if files are moved or volumes are remounted.

### Backup

To fully back up an instance, preserve these three paths:

- `/app/data/` (database)
- `/captures/` (images)
- `/timelapses/` (videos)

The database contains all job configurations, capture metadata, and video records. Without it, the application cannot associate images with their jobs.

### Job Deletion

Deleting a job permanently removes the job configuration and all associated capture images from both the database and the filesystem. Timelapse videos that were created from the job's captures are preserved but will no longer show a job association.

If you want to stop a job without losing data, disable it or let it complete naturally instead of deleting it.

### Network and Camera Reliability

FFmpeg capture operations time out based on the `FFMPEG_TIMEOUT` setting. If your cameras are on a slow or unreliable network, increase this value. The scheduler tracks consecutive failures per job and sets a warning after reaching the configurable threshold (default: 3). If webhook notifications are configured in Settings, an alert is sent when the threshold is first reached. Jobs are never automatically disabled by failures; manual intervention is required to stop a persistently failing job.

RTSP captures use TCP transport for reliability over UDP. Ensure the container can reach your camera network and that any firewalls allow the RTSP port (typically 554 or 7441 for UniFi Protect).

## Technology Stack

- Python 3.11 with FastAPI and Uvicorn
- SQLite for data storage
- FFmpeg for image capture and video encoding
- Pillow for thumbnail generation
- Vanilla HTML, CSS, and JavaScript frontend with Alpine.js
- Chart.js for storage dashboard visualizations
- Docker with multi-stage builds

## CI/CD

The repository includes three GitHub Actions workflows:

- **Build Validation** runs on every push to main and on pull requests. It builds the Docker image and runs a smoke test against the health endpoint. It does not push images to the registry.
- **Release** triggers on version tags (v*.*.*). It builds and pushes the image to the GitHub Container Registry (ghcr.io) with both the version tag and `latest`.
- **Dependency Updates** runs weekly. It compiles updated Python dependencies, runs a security scan, and opens a pull request if changes are found.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Author

Maintained by [kernelkaribou](https://github.com/kernelkaribou)

## Disclaimer

This was a shell script I had personally made before but wanted something easier to configure and schedule on a larger scale. This application was built almost exclusively using vibe 🤮 coding. If you are reading this you should know because some people get upset. This was an idea I carried around for a while and AI made it actually happen. I can't imagine that I will be making much changes to it as its more of a utility than an application but intend to keep it functioning as long as I can. Do whatever you want with it, I don't care.