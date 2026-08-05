#!/usr/bin/env bash
set -Eeuo pipefail

gui_enabled="${KORAIL_BROWSER_GUI_ENABLED:-false}"
if [[ "${gui_enabled}" == "false" ]]; then
  exec "$@"
fi
if [[ "${gui_enabled}" != "true" ]]; then
  echo "KORAIL_BROWSER_GUI_ENABLED must be true or false" >&2
  exit 64
fi

password_file="${KORAIL_NOVNC_PASSWORD_FILE:-}"
if [[ -z "${password_file}" || ! -r "${password_file}" ]]; then
  echo "GUI mode requires a readable KORAIL_NOVNC_PASSWORD_FILE" >&2
  exit 64
fi

export DISPLAY="${DISPLAY:-:99}"
runtime_dir=/tmp/korail-gui
mkdir -p "${runtime_dir}"
chmod 0700 "${runtime_dir}"
export XAUTHORITY="${runtime_dir}/Xauthority"
touch "${XAUTHORITY}"
chmod 0600 "${XAUTHORITY}"

cookie="$(mcookie)"
xauth -f "${XAUTHORITY}" add "${DISPLAY}" . "${cookie}"
unset cookie

vnc_password_file="${runtime_dir}/vnc-password"
umask 077
python - "${password_file}" "${vnc_password_file}" <<'PY'
import os
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_bytes()
if raw.endswith(b"\r\n"):
    raw = raw[:-2]
elif raw.endswith(b"\n"):
    raw = raw[:-1]
if len(raw) != 8 or any(byte < 0x21 or byte > 0x7E for byte in raw):
    raise SystemExit(
        "KORAIL noVNC password must be exactly 8 printable ASCII bytes; "
        "classic VNC authentication ignores additional bytes"
    )
target = Path(sys.argv[2])
target.write_bytes(raw + b"\n")
os.chmod(target, 0o600)
PY
unset KORAIL_NOVNC_PASSWORD_FILE

geometry="${KORAIL_VNC_GEOMETRY:-1440x1000}"
Xvfb "${DISPLAY}" -screen 0 "${geometry}x24" -nolisten tcp -auth "${XAUTHORITY}" \
  >"${runtime_dir}/xvfb.log" 2>&1 &
pids=("$!")

for _ in $(seq 1 100); do
  if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
  echo "Xvfb did not become ready" >&2
  exit 70
fi

openbox --sm-disable >"${runtime_dir}/openbox.log" 2>&1 &
pids+=("$!")
x11vnc -display "${DISPLAY}" -auth "${XAUTHORITY}" -localhost -rfbport 5900 \
  -forever -shared -viewonly -noclipboard -nosetclipboard -passwdfile "${vnc_password_file}" \
  -quiet >"${runtime_dir}/x11vnc.log" 2>&1 &
pids+=("$!")
websockify --web=/usr/share/novnc 6080 127.0.0.1:5900 \
  >"${runtime_dir}/websockify.log" 2>&1 &
pids+=("$!")

port_ready() {
  python - "$1" <<'PY'
import socket
import sys

try:
    connection = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.2)
except OSError:
    raise SystemExit(1) from None
connection.close()
PY
}

for _ in $(seq 1 100); do
  if port_ready 5900 && port_ready 6080; then
    break
  fi
  sleep 0.1
done
if ! port_ready 5900 || ! port_ready 6080; then
  echo "VNC/noVNC proxy did not become ready" >&2
  exit 70
fi

"$@" &
pids+=("$!")

shutdown() {
  trap - EXIT INT TERM
  kill "${pids[@]}" 2>/dev/null || true
  for _ in $(seq 1 50); do
    alive=false
    for pid in "${pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive=true
      fi
    done
    if [[ "${alive}" == "false" ]]; then
      wait "${pids[@]}" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done
  kill -KILL "${pids[@]}" 2>/dev/null || true
  wait "${pids[@]}" 2>/dev/null || true
}
trap shutdown EXIT INT TERM

set +e
wait -n "${pids[@]}"
status=$?
set -e
exit "${status}"
