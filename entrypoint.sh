#!/bin/bash
set -e

# Default PUID and PGID to 0 (root) if not set
PUID=${PUID:-0}
PGID=${PGID:-0}

# If running as root, skip user creation
if [ "$PUID" = "0" ] && [ "$PGID" = "0" ]; then
    exec "$@"
fi

# Create app user if it doesn't exist
if ! id -u appuser > /dev/null 2>&1; then
    groupadd -g "$PGID" appgroup
    useradd -u "$PUID" -g "$PGID" -d /app -s /bin/bash appuser
fi

# Update existing user's UID/GID if they differ
CURRENT_UID=$(id -u appuser 2>/dev/null || echo 0)
CURRENT_GID=$(id -g appuser 2>/dev/null || echo 0)

if [ "$CURRENT_UID" != "$PUID" ] || [ "$CURRENT_GID" != "$PGID" ]; then
    groupmod -g "$PGID" appgroup 2>/dev/null || true
    usermod -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
fi

echo "ChronoSnap: preparing recursive app-data and import-staging ownership for $PUID:$PGID"
for directory in /app/data /app/import-staging; do
    if ! chown -R "$PUID:$PGID" "$directory"; then
        echo "ChronoSnap: WARNING: cannot fully change ownership of $directory; configure permissions on the host so $PUID:$PGID has read/write access to its contents." >&2
    fi
done

# Never walk existing media archives: recursive ownership changes can stall on NFS.
echo "ChronoSnap: preparing top-level media directory ownership (existing media files are unchanged)"
for directory in /captures /timelapses /imports; do
    if ! CURRENT_OWNER=$(stat -c '%u:%g' "$directory"); then
        echo "ChronoSnap: WARNING: cannot inspect ownership of $directory; check the mount and host/NFS permissions for $PUID:$PGID. Skipping ownership preparation for this directory." >&2
        continue
    fi
    if [ "$CURRENT_OWNER" != "$PUID:$PGID" ]; then
        if ! chown "$PUID:$PGID" "$directory"; then
            echo "ChronoSnap: WARNING: cannot change ownership of $directory; configure permissions on the host/NFS server so $PUID:$PGID has the required access." >&2
        fi
    fi
done
echo "ChronoSnap: ownership preparation complete; existing media directories and files must already have suitable permissions"

# Add appuser to the video group for V4L2 device access
if getent group video > /dev/null 2>&1; then
    usermod -aG video appuser 2>/dev/null || true
fi

# Switch to app user and execute the command
exec gosu appuser "$@"
