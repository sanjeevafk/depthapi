#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env"
LOCAL_FILE=".env.local"
CLOUD_FILE=".env.cloud"
AUTO_BACKUP_FILE=".env.cloud.auto"

usage() {
  echo "Usage: scripts/switch_env.sh <local|cloud|status>"
}

current_mode() {
  if [ ! -f "$ENV_FILE" ]; then
    echo "missing"
    return
  fi
  if cmp -s "$ENV_FILE" "$LOCAL_FILE" 2>/dev/null; then
    echo "local"
    return
  fi
  if cmp -s "$ENV_FILE" "$CLOUD_FILE" 2>/dev/null; then
    echo "cloud"
    return
  fi
  if cmp -s "$ENV_FILE" "$AUTO_BACKUP_FILE" 2>/dev/null; then
    echo "cloud(auto)"
    return
  fi
  echo "custom"
}

cmd="${1:-}"
case "$cmd" in
  local)
    [ -f "$LOCAL_FILE" ] || { echo "Missing $LOCAL_FILE"; exit 1; }
    if [ -f "$ENV_FILE" ] && [ ! -f "$CLOUD_FILE" ]; then
      cp "$ENV_FILE" "$AUTO_BACKUP_FILE"
    fi
    cp "$LOCAL_FILE" "$ENV_FILE"
    echo "Switched to local env (.env <- .env.local)"
    ;;
  cloud)
    if [ -f "$CLOUD_FILE" ]; then
      cp "$CLOUD_FILE" "$ENV_FILE"
      echo "Switched to cloud env (.env <- .env.cloud)"
    elif [ -f "$AUTO_BACKUP_FILE" ]; then
      cp "$AUTO_BACKUP_FILE" "$ENV_FILE"
      echo "Switched to cloud env (.env <- .env.cloud.auto)"
    else
      echo "Missing .env.cloud and .env.cloud.auto"
      exit 1
    fi
    ;;
  status)
    echo "mode=$(current_mode)"
    ;;
  *)
    usage
    exit 1
    ;;
esac
