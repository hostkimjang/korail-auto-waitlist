FROM postgres:16-alpine

RUN apk add --no-cache age tzdata && \
    mkdir -p /backups && chown postgres:postgres /backups

COPY --chmod=0555 scripts/backup-postgres.sh scripts/restore-postgres.sh /scripts/

USER postgres
ENTRYPOINT ["/scripts/backup-postgres.sh"]
CMD ["daemon"]
