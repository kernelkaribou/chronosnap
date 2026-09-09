---
applyTo: "backend/**/*.py"
---

# Backend (FastAPI / Python) Conventions

- **No ORM.** Raw SQL via the stdlib `sqlite3` module, everywhere. Always use
  parameterized queries (`?` placeholders) — never format user input directly
  into SQL strings.
- **`backend/utils.py` stays a module, not a package.** Don't split it into a
  `utils/` directory; it's small and deliberately flat (`get_now`, `to_iso`,
  `parse_iso`, `ensure_timezone_aware`).
- **Config via `backend/config.py`.** New environment-variable-driven settings
  belong there, following the existing pattern:
  `SOMETHING_ENABLED = os.getenv("SOMETHING", "true").strip().lower() not in ("false", "0", "no", "off")`
  (accept common falsy spellings, not just `"false"`).
- **Routers** live under `backend/routers/`, one file per resource, registered in
  `backend/app.py` via `app.include_router(..., prefix="/api/<resource>", dependencies=[Depends(verify_api_key)])`.
  Every new router needs that `verify_api_key` dependency unless there's a
  specific, documented reason not to (e.g. `/health`, static file serving).
- **Helpers over duplication.** Use `backend/helpers/db_helpers.py`
  (`get_or_404`, `ensure_column`, `enrich_capture`, `normalize_favorite`) and
  `backend/helpers/file_helpers.py` (`validate_writable_directory`,
  `delete_capture_file`, `delete_video_files`, `cleanup_empty_parents`) instead
  of re-implementing the same lookup/cleanup logic in a new router.
- **Path safety.** Any endpoint that resolves a path from stored/user-influenced
  data (capture paths, video paths, import file names) must go through the
  existing `resolve_*_path()` helpers — never build filesystem paths by naive
  string concatenation, to avoid path traversal.
- **Events vs. webhooks are different systems, don't assume one implies the
  other:**
  - `add_event(...)` (`backend/services/event_service.py`) logs to the in-app
    event log. It's called from job create/delete, video build
    complete/fail/delete, scheduler job-completion, auto-build, and import
    flows. Manual single-shot actions (`POST /api/jobs/{id}/capture`) do
    **not** log an event.
  - `send_webhook_event(...)` (`backend/services/webhook.py`) is only fired by
    the scheduler (`completed`/`warning`/`recovered`) and auto-builder
    (`auto_build_complete`). A manually built video or manual capture does
    **not** trigger a webhook. Use `POST /api/settings/webhook/test` to test
    webhook delivery directly without needing to reproduce a scheduler event.
- **Job deletion vs. video deletion are different contracts:** `DELETE
  /api/jobs/{id}` removes the job's capture records/files but **preserves**
  any videos already built from it (their `job_id` is set to `NULL`). Deleting
  the videos themselves is a separate `DELETE /api/videos/{id}` call, which
  removes the video/thumbnail files and cleans up empty parent directories via
  `cleanup_empty_parents`.
- **Logging:** never log the API key, a full stream URL that may contain
  embedded credentials, or raw image bytes.
