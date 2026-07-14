# Release D Acceptance

Commit: __________  Date: __________  Environment: __________  Executor: __________

- [ ] Full PostgreSQL test suite and four HTTP critical-flow loops
- [ ] Queue concurrency, retry/dead, and stale recovery evidence
- [ ] Upload/access security and SQL/performance budgets
- [ ] Backup checksum and isolated restore row-count comparison
- [ ] `audit.schema_migration(version, checksum)` equality verified
- [ ] `/healthz` and `/readyz` evidence captured

Record commands, timestamps, backup filename/checksum, restore database, and residual limitations beside each item.
