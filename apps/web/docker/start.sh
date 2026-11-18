#!/bin/sh
# Write the Android app-link settings this install passes in from .env, then run nginx. The bundle
# loads /runtime-config.js before it starts, so an official release image follows .env without a
# rebuild. Only "true"/"false" switches and short version strings pass; anything else is left out,
# which keeps that link off because the bundle needs both a switch and a validated version.
set -eu
directory=/tmp/railwait-runtime
mkdir -p "$directory"
settings=''
separator=''
add_setting() {
  settings="$settings$separator\"$1\":\"$2\""
  separator=','
}
for name in VITE_KORAIL_BOOKING_DEEPLINK_ENABLED VITE_KORAIL_TICKET_DEEPLINK_ENABLED \
  VITE_SRT_MAIN_DEEPLINK_ENABLED VITE_SRT_TICKET_DEEPLINK_ENABLED; do
  value="$(printenv "$name" || true)"
  case "$value" in
    true | false) add_setting "$name" "$value" ;;
    '') ;;
    *) echo "railwait-web: ignored $name (expected true or false)" >&2 ;;
  esac
done
for name in VITE_KORAIL_BOOKING_VALIDATED_VERSION VITE_KORAIL_TICKET_VALIDATED_VERSION \
  VITE_SRT_MAIN_VALIDATED_VERSION VITE_SRT_TICKET_VALIDATED_VERSION; do
  value="$(printenv "$name" || true)"
  if [ -z "$value" ]; then
    continue
  elif printf '%s' "$value" | grep -Eq '^[0-9A-Za-z.+_-]{1,64}$'; then
    add_setting "$name" "$value"
  else
    echo "railwait-web: ignored $name (letters, digits and .+_- up to 64 characters)" >&2
  fi
done
printf 'window.__RAILWAIT_RUNTIME_CONFIG__ = {%s};\n' "$settings" > "$directory/runtime-config.js.tmp"
mv "$directory/runtime-config.js.tmp" "$directory/runtime-config.js"
exec nginx -g 'daemon off;'
