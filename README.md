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
- URL validation with a preview capture before committing to a job
- Preview button on existing jobs to verify camera connectivity and angle at any time
- Manual snapshot trigger for on-demand captures outside the schedule

### Video Processing

- Generate timelapse videos from captured images using FFmpeg
- Configurable resolution, framerate, and quality (low, medium, high, lossless)
- Native resolution option to match source capture dimensions
- Filter captures by time range, capture ID range, or tag when building a video
- Tag picker in the build modal for filtering captures by tag
- Real-time progress tracking with the ability to cancel in-progress builds
- Full-resolution preview images in the build modal (not thumbnails)
- Text overlay options: percentage-based font sizing, opacity slider, and position grid
- Videos are preserved even if the parent job is deleted
- **Auto-build**: per-job recurring timelapse generation (daily, weekly, monthly, etc.) — videos accumulate as a sequence with an "Auto" badge in the gallery. Next auto-build time is displayed on job cards and in the detail view.
- **Shared videos**: videos can be shared via a public link. Shared videos display an indicator icon on their cards, and the timelapses page includes a filter toggle to show only shared videos.

### Importing

The import feature allows bringing in existing images and videos from outside the normal capture workflow. This is useful for consolidating footage from other sources, importing archives from previous setups, or managing standalone timelapse videos.

**Server Path Import**: Place files in the `/imports` directory (or any configured import path) on the host, then browse and select them from within the web interface. The default import path is configurable in Settings.

**Browser Upload**: Drag and drop files or folders directly into the import modal, or use the Browse button to select a folder. Uploads support recursive folder traversal for nested directory structures.

**How it works**: Each import operation goes through a staging pipeline — files are scanned, analyzed (classified as images or videos, checked for duplicates), and previewed before confirming. Once confirmed, images are moved into the standard capture directory structure and a new job is created for them. Videos are imported as standalone timelapses in the gallery.

**Important**: Each import creates a single job. If you need to import images into separate jobs, perform separate imports. Videos are imported individually and do not require a job — they appear directly in the timelapse gallery with an "Imported" badge.

**Supported formats**:
- **Images**: JPEG, PNG, BMP, TIFF, WebP
- **Videos**: MP4, AVI, MOV, MKV, WebM, M4V
- **Archives**: ZIP, TAR, GZ, TGZ, BZ2, RAR, 7Z (automatically extracted during analysis)

**Duplicate detection**: Videos are checked against existing imports using SHA-256 content hashing and file size with duration matching. Duplicates are identified during the preview stage and blocked from being imported again, even if the filename has changed.

After a successful import, source files are removed from the import directory to prevent accidental re-imports.

### Exporting

Export a job as a ZIP archive containing all of its captures (with the original date-based directory structure), generated videos with thumbnails, and a `job.json` metadata file. The metadata includes the job configuration and capture/video counts for reference or potential future re-import. Stream URL credentials are automatically redacted in the exported `job.json`.

**Small exports** (under 1 GB) are streamed directly as a browser download using temporary files instead of in-memory buffering. **Large exports** (1 GB or more) are built to the `/exports` directory on disk, then downloaded from there. Export filenames include a timestamp to prevent concurrent overwrites. Symlinks are skipped during export for security. You can manage saved exports from the API — list, download, or delete them.

**Export retention**: configurable in Settings with a default of 7 days. Old exports are automatically cleaned up at container startup. Set retention to 0 to keep exports indefinitely.

The export button appears in the job details modal (the download arrow icon next to edit/duplicate).

### Management

- Web interface with light and dark themes
- 12-hour and 24-hour time display toggle
- Job search bar to filter jobs by name
- Multi-select status toggle buttons (Active, Sleeping, Completed, Disabled, Warning) and sort options
- Warning is an API-computed status based on consecutive capture failures
- Per-job capture sync tool to reconcile database records with files on disk
- Orphaned capture detection across all jobs
- Bulk capture deletion
- Job duplication to quickly create similar configurations
- API key authentication (32-character keys) for all endpoints
- Health check endpoint for container orchestration
- **Server paths**: all four paths (Captures, Timelapses, Import, Export) are configurable in Settings, each with a default matching its Docker volume mount. Paths use an edit/confirm/cancel toggle pattern consistent with job path editing.

### Storage Dashboard

- Visual breakdown of storage usage across all jobs
- Donut charts for captures vs. timelapses and disk usage
- Per-job horizontal bar chart showing capture and timelapse sizes
- Summary cards for total captures, timelapses, storage used, and disk free

### Webhook Notifications

- Event-driven webhook notifications for job state changes
- Events: **warning** (consecutive capture failures), **recovered** (success after warning), **completed** (job finished its schedule)
- Configurable event filtering: choose which events trigger webhooks (warning, recovered, completed) in Settings. Previously all events fired; now each can be individually enabled or disabled.
- Per-job warning threshold configurable from 1 to 50 consecutive failures (default: 3)
- JSON payload template with variable substitution for integration with Home Assistant, Discord, Slack, or any HTTP endpoint
- Available template variables: `{title}`, `{message}`, `{event}`, `{job_name}`, `{job_id}`, `{failure_count}`, `{error_message}`
- Test button to verify webhook configuration before relying on it
- Alerts fire once per state transition (non-spamming)

### Version Management

- The `VERSION` file at the repository root is the single source of truth for the application version
- The Settings page displays the current version and checks GitHub releases for available updates
- An "Update available" badge appears when a newer release is found, linking directly to the release page
- Cache-busting for static assets is handled automatically using a hash derived from the version string

## Quick Start

```bash
docker-compose up -d
```

The web interface is available at `http://<host>:8080`. On first launch, an API key is generated automatically and displayed in the Settings view.

## Docker Configuration

### Volumes

Five volumes are required for persistent data:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./captures` | `/captures` | Captured images organized by job |
| `./timelapses` | `/timelapses` | Processed timelapse videos |
| `./data` | `/app/data` | SQLite database and import staging area |
| `./imports` | `/imports` | Drop zone for server-side file imports |
| `./exports` | `/exports` | Staging area for large job exports |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `0` | User ID for file ownership. Set this to match your host user to avoid permission issues. |
| `PGID` | `0` | Group ID for file ownership. |
| `TZ` | `Etc/UTC` | Timezone for scheduling and display. Must be a valid IANA timezone (for example, `America/Chicago`). This controls when time windows activate and how timestamps are displayed. |
| `PORT` | `8080` | Port the application listens on inside the container. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `MAX_UPLOAD_SIZE` | `10737418240` | Maximum upload size in bytes (default 10 GB). |
| `APP_VERSION` | Read from `VERSION` file | Override the application version string. Optional — if not set, the version is read from the `VERSION` file at the repository root. |

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
      - ./imports:/imports
      - ./exports:/exports
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
| Capture Path | No | Directory where captures are stored. Defaults to the captures path configured in Settings. Read-only by default with an edit/confirm/cancel toggle. |
| Naming Pattern | No | Template for capture filenames. Uses `{count}` as the sequence placeholder (for example, `jobname_{count}`). A live preview shows an example filename as you type. Backward compatible with existing jobs that use the `{num:06d}` format. |
| Start Date/Time | Yes | When the job begins capturing. |
| End Date/Time | No | When the job stops capturing. If omitted, the job runs indefinitely until manually stopped. |
| Interval | Yes | Seconds between captures. Minimum 10 seconds. |
| Framerate | No | Default framerate for generated videos. Defaults to 30 fps. |
| Time Window | No | When enabled, restricts captures to a daily window defined by a start and end time in HH:MM format. |

### Job States

Jobs transition between five states based on their schedule, configuration, and health:

- **Active**: The job is within its scheduled date range and, if a time window is configured, within the active window. Captures are being taken at the defined interval.
- **Sleeping**: The job is within its date range but outside its daily time window, or the start date has not arrived yet. No captures are taken.
- **Completed**: The end date has passed. The job and its captures remain available for video processing.
- **Disabled**: Manually paused by the user. Can be re-enabled at any time.
- **Warning**: The job has exceeded its consecutive capture failure threshold. This is computed by the API based on the job's warning threshold setting. The job continues attempting captures and transitions back to its normal state once a capture succeeds.

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
| `/api/jobs` | Create, list, update, and delete capture jobs. Trigger manual captures. Run capture sync scans. Export jobs as ZIP archives. |
| `/api/captures` | List, filter, download, and delete captures. Detect and clean up orphaned files. |
| `/api/videos` | Create timelapse videos, track processing progress, download and delete videos. |
| `/api/import` | Import images and videos from server paths or browser uploads. Browse directories, analyze staged files, execute imports. |
| `/api/settings` | View and regenerate the API key. Configure webhook notifications (URL, template, event filtering), server paths (captures, timelapses, import, export), export retention, and check for version updates. |
| `/api/storage` | Storage statistics and disk usage. |

## Considerations

### Timezone Configuration

The `TZ` environment variable is critical for correct scheduling behavior. All capture grid calculations convert through UTC to handle daylight saving time transitions correctly. If the container timezone does not match your intended schedule, time windows and capture times will be offset.

Changing the timezone on a running instance will not retroactively adjust existing job schedules. Jobs calculate their next capture relative to their original start datetime, so the grid remains consistent.

### Storage Planning

Captured images are typically 50 KB to 500 KB each depending on the source resolution and scene complexity. A job capturing once per minute generates roughly 1,440 images per day. At 200 KB average, that is approximately 280 MB per day or about 8.5 GB per month.

Plan your storage volumes accordingly for long-running jobs. The capture sync tool can help identify discrepancies between the database and filesystem if files are moved or volumes are remounted.

### Backup

To fully back up an instance, preserve these paths:

- `/app/data/` (database)
- `/captures/` (images)
- `/timelapses/` (videos)

The `/imports/` directory does not need to be backed up — it is a temporary drop zone for files being imported. After a successful import, files are moved out of this directory automatically.

The `/exports/` directory contains saved export archives for large jobs. These can be re-downloaded or deleted from the API and do not need to be backed up unless you want to preserve them separately.

The database contains all job configurations, capture metadata, and video records. Without it, the application cannot associate images with their jobs.

### Job Deletion

Deleting a job permanently removes the job configuration and all associated capture images from both the database and the filesystem. Timelapse videos that were created from the job's captures are preserved but will no longer show a job association.

If you want to stop a job without losing data, disable it or let it complete naturally instead of deleting it.

### Network and Camera Reliability

FFmpeg capture operations time out based on the `FFMPEG_TIMEOUT` setting. If your cameras are on a slow or unreliable network, increase this value. The scheduler tracks consecutive failures per job and triggers a warning after reaching the job's configured threshold (default: 3 failures). Each job can have its own threshold — set low (1-2) for once-a-day captures where every miss matters, or higher for frequent captures where brief outages are tolerable. If webhook notifications are enabled in Settings, events fire on state transitions: when a job enters warning state, when it recovers after being in warning, and when it completes its schedule. Jobs are never automatically disabled by failures; manual intervention is required to stop a persistently failing job.

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