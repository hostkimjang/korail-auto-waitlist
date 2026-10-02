#!/bin/sh
set -eu

# Recreate the narrowly scoped role on every Redis start. The ACL file lives in
# tmpfs; only the password hash is written and the password never becomes argv.
umask 077
acl_file=/tmp/rail-waitlist-users.acl
printf '%s\n' 'user default on nopass ~* &* +@all' > "$acl_file"
if [ -n "${KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD:-}" ]; then
    password_hash=$(printf '%s' "$KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD" | sha256sum)
    password_hash=${password_hash%% *}
    printf '%s\n' "user korail-cooldown on #${password_hash} ~rail-waitlist:korail:provider-admission:v1 -@all +eval +time +hgetall +hget +hset +pexpire +ping +select +client|setinfo" >> "$acl_file"
fi
unset KORAIL_PROVIDER_COOLDOWN_REDIS_PASSWORD password_hash
exec redis-server --appendonly yes --appendfsync everysec --aclfile "$acl_file"
