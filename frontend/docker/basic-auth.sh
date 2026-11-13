#!/bin/sh
# Writes the password file nginx checks. Without credentials the container stops instead of
# serving the UI to anyone.
set -eu

if [ -z "${UI_USERNAME:-}" ] || [ -z "${UI_PASSWORD:-}" ]; then
    echo "basic-auth: UI_USERNAME and UI_PASSWORD must be set" >&2
    exit 1
fi

hash=$(printf '%s' "$UI_PASSWORD" | mkpasswd -m sha512 -P 0)
printf '%s:%s\n' "$UI_USERNAME" "$hash" > /etc/nginx/htpasswd
