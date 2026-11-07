#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# The setup instructions put scrcpy here without requiring a shell restart.
export PATH="$HOME/.local/bin:$PATH"
exec python3 -m phone_link "$@"
