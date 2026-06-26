#!/usr/bin/env bash
# Portable lock helpers (macOS launchd has no flock).

acquire_lock() {
  local lock_dir="$1"
  if mkdir "$lock_dir" 2>/dev/null; then
    echo $$ >"$lock_dir/pid"
    trap 'release_lock "$lock_dir"' EXIT
    return 0
  fi

  if [[ -f "$lock_dir/pid" ]]; then
    local holder
    holder="$(cat "$lock_dir/pid" 2>/dev/null || true)"
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
      return 1
    fi
    rm -rf "$lock_dir"
    if mkdir "$lock_dir" 2>/dev/null; then
      echo $$ >"$lock_dir/pid"
      trap 'release_lock "$lock_dir"' EXIT
      return 0
    fi
  fi

  return 1
}

release_lock() {
  local lock_dir="$1"
  rm -rf "$lock_dir"
}
