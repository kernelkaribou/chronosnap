#!/bin/bash
set -euo pipefail

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

app_directories=(/app/data /app/import-staging)
media_directories=(/captures /timelapses /imports)
directories=("${app_directories[@]}" "${media_directories[@]}")
mkdir -p /tmp/startup-bin
cat > /tmp/startup-bin/chown <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> /tmp/startup-chown.log
if [ "${FAIL_CHOWN_DIRECTORY:-}" = "${*: -1}" ]; then
    echo "chown: changing ownership of '${*: -1}': Operation not permitted" >&2
    exit 1
fi
exec /usr/bin/chown "$@"
EOF
chmod +x /tmp/startup-bin/chown
cat > /tmp/startup-bin/stat <<'EOF'
#!/bin/bash
if [ "${FAIL_STAT_DIRECTORY:-}" = "${*: -1}" ]; then
    echo "stat: cannot stat '${*: -1}': Permission denied" >&2
    exit 1
fi
exec /usr/bin/stat "$@"
EOF
chmod +x /tmp/startup-bin/stat
export PATH="/tmp/startup-bin:$PATH"
export PUID=1000 PGID=1000

for directory in "${directories[@]}"; do
    mkdir -p "$directory/existing/private"
    printf 'existing data\n' > "$directory/existing/private/sentinel"
    /usr/bin/chown -R 0:0 "$directory"
    chmod 755 "$directory/existing/private"
done
for directory in "${media_directories[@]}"; do
    chmod 700 "$directory/existing/private"
done

if ! output=$(/entrypoint.sh bash -ec '
    test "$(id -u)" = 1000
    test "$(id -g)" = 1000
    for directory in /app/data /app/import-staging /captures /timelapses; do
        touch "$directory/new-file"
    done
    for directory in /app/data /app/import-staging; do
        test "$(cat "$directory/existing/private/sentinel")" = "existing data"
        touch "$directory/existing/private/new-file"
    done
    echo "APP_STARTED"
' 2>&1); then
    fail "App user could not access app-managed files: $output"
fi
[[ "$output" == *APP_STARTED* ]] || fail "$output"

for directory in "${media_directories[@]}"; do
    [[ "$(stat -c '%u:%g' "$directory/existing/private/sentinel")" = 0:0 ]] ||
        fail "Startup changed existing file ownership"
    [[ "$(cat "$directory/existing/private/sentinel")" = "existing data" ]] ||
        fail "Startup changed existing data"
done

[[ "$output" == *"top-level"* ]] || fail "No ownership preparation log"
[[ "$output" == *"complete"* ]] || fail "No ownership completion log"
[[ "$(wc -l < /tmp/startup-chown.log)" = 5 ]] || fail "Expected five chown calls"
for directory in "${app_directories[@]}"; do
    grep -Fxq -- "-R 1000:1000 $directory" /tmp/startup-chown.log ||
        fail "App-managed directory must be chowned recursively"
done
for directory in "${media_directories[@]}"; do
    grep -Fxq "1000:1000 $directory" /tmp/startup-chown.log ||
        fail "Media ownership changes must be top-level only"
done
for directory in "${directories[@]}"; do
    [[ "$(stat -c '%u:%g' "$directory")" = 1000:1000 ]] ||
        fail "Top-level ownership not initialized"
done

for directory in "${app_directories[@]}"; do
    /usr/bin/chown 0:0 "$directory/new-file"
done
: > /tmp/startup-chown.log
/entrypoint.sh bash -ec '
    for directory in /app/data /app/import-staging; do
        test "$(stat -c "%u:%g" "$directory/new-file")" = 1000:1000
        printf "updated\n" >> "$directory/new-file"
    done
'
[[ "$(wc -l < /tmp/startup-chown.log)" = 2 ]] ||
    fail "Restart must recurse into app directories but skip matching media owners"
for directory in "${app_directories[@]}"; do
    grep -Fxq -- "-R 1000:1000 $directory" /tmp/startup-chown.log ||
        fail "Matching app-directory owner must not skip existing files"
done

for directory in /app/data /imports; do
    /usr/bin/chown 0:0 /imports
    output=$(FAIL_CHOWN_DIRECTORY="$directory" /entrypoint.sh echo APP_STARTED 2>&1)
    [[ "$output" == *APP_STARTED* ]] || fail "Ownership failure blocked execution: $output"
    [[ "$output" == *WARNING* && "$output" == *"$directory"* ]] ||
        fail "Ownership failure needs an actionable warning: $output"
done

if ! output=$(FAIL_STAT_DIRECTORY=/captures /entrypoint.sh echo APP_STARTED 2>&1); then
    fail "Ownership lookup failure blocked execution: $output"
fi
[[ "$output" == *APP_STARTED* && "$output" == *WARNING* && "$output" == *"/captures"* ]] ||
    fail "Ownership lookup failure needs a warning and continued execution: $output"

: > /tmp/startup-chown.log
output=$(PUID=0 PGID=0 /entrypoint.sh id -u)
[[ "$output" = 0 ]] || fail "Root execution changed"
[[ ! -s /tmp/startup-chown.log ]] || fail "Root bypass attempted chown"

output=$(gosu 1000:1000 env PUID=0 PGID=0 /entrypoint.sh bash -ec '
    test "$(id -u)" = 1000
    touch /app/import-staging/direct-user
    echo "DIRECT_USER_STARTED"
')
[[ "$output" == *DIRECT_USER_STARTED* ]] || fail "Explicit non-root user failed"
[[ ! -s /tmp/startup-chown.log ]] || fail "Explicit user attempted chown"

echo "PASS: recursive app ownership, top-level-only media ownership, restart, permission warnings, root and explicit-user startup"
