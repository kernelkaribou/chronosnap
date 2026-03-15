#!/bin/bash
set -e

# Default PUID and PGID to 0 (root) if not set
PUID=${PUID:-0}
PGID=${PGID:-0}

echo "Starting with PUID=$PUID and PGID=$PGID"

# If running as root, skip user creation
if [ "$PUID" = "0" ] && [ "$PGID" = "0" ]; then
    echo "Running as root"
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
    echo "Updating appuser UID to $PUID and GID to $PGID"
    groupmod -g "$PGID" appgroup 2>/dev/null || true
    usermod -u "$PUID" -g "$PGID" appuser 2>/dev/null || true
fi

# Set ownership of directories (ignore errors for NFS/read-only mounts)
chown -R "$PUID:$PGID" /app/data /captures /timelapses 2>/dev/null || true

# Switch to app user and execute the command
exec gosu "$PUID:$PGID" "$@"
