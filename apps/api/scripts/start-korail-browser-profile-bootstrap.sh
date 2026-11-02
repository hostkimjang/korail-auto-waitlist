#!/usr/bin/env bash
set -Eeuo pipefail

profile_dir="${KORAIL_BROWSER_PROFILE_DIR:-}"
marker_name=.rail-waitlist-korail-profile
marker_content=rail-waitlist-korail-browser-profile-v1
owner_lock_name=.rail-waitlist-korail-profile-owner.lock

if [[ -z "${profile_dir}" || "${profile_dir}" != /* ]]; then
  echo "KORAIL_BROWSER_PROFILE_DIR must be an absolute dedicated directory" >&2
  exit 64
fi
if [[ -L "${profile_dir}" ]]; then
  echo "KORAIL_BROWSER_PROFILE_DIR must not be a symbolic link" >&2
  exit 64
fi

umask 077
mkdir -p "${profile_dir}"
chmod 0700 "${profile_dir}"
marker_path="${profile_dir}/${marker_name}"
if [[ -e "${marker_path}" && ! -f "${marker_path}" ]]; then
  echo "KORAIL browser profile marker is invalid" >&2
  exit 64
fi
if [[ -f "${marker_path}" ]] && [[ "$(<"${marker_path}")" != "${marker_content}" ]]; then
  echo "KORAIL browser profile marker is invalid" >&2
  exit 64
fi
printf '%s\n' "${marker_content}" >"${marker_path}"
chmod 0600 "${marker_path}"

owner_lock_path="${profile_dir}/${owner_lock_name}"
exec 9>"${owner_lock_path}"
chmod 0600 "${owner_lock_path}"
if ! flock -n 9; then
  echo "KORAIL dedicated browser profile is already in use" >&2
  exit 73
fi

for singleton_name in SingletonLock SingletonSocket SingletonCookie; do
  singleton_path="${profile_dir}/${singleton_name}"
  if [[ -L "${singleton_path}" ]]; then
    rm -- "${singleton_path}"
  elif [[ -e "${singleton_path}" ]]; then
    echo "KORAIL browser profile singleton is not a symbolic link" >&2
    exit 64
  fi
done

echo "KORAIL dedicated browser profile initialized"
