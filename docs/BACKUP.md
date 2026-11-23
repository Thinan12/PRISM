# Backup and recovery

PRISM state = PostgreSQL metadata + the `prism-data` volume (immutable originals, Parquet versions, workbooks). Back up both together.

```bash
scripts/backup.sh /srv/backups                             # writes prism-<timestamp>/{db.dump,data.tar.gz,SHA256SUMS}
scripts/restore.sh /srv/backups/prism-20261123-020000      # destructive; verifies checksums and asks for confirmation
```

Recommended: a nightly cron job (`0 2 * * * cd /opt/prism && scripts/backup.sh /srv/backups`), off-server copies, and a quarterly test restore on staging. Dataset files are content-addressed and never modified after writing, so incremental file backups (rsync/restic) of the data volume are safe.

> The scripts have not yet been run against a live deployment (see DEPLOYMENT.md status note).
