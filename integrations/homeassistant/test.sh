#!/usr/bin/env bash
# The HA layer's tests, in Home Assistant's own harness (Python 3.13, not the repo's env).
#   integrations/homeassistant/test.sh [pytest args]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env="${FIXIT_HA_TEST_ENV:-${XDG_CACHE_HOME:-$HOME/.cache}/fixit-ha-test}"
if [ ! -x "$env/bin/pytest" ]; then
  uv venv -q --python 3.13 "$env"
  VIRTUAL_ENV="$env" uv pip install -q pytest-homeassistant-custom-component
fi
cd "$here" && exec "$env/bin/pytest" tests -q -p no:cacheprovider "$@"
