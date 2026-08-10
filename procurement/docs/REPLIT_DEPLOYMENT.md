# Replit Deployment Plan — V1

## Topology

### Autoscale web service
Runs:
- dashboard;
- review UI;
- price-book upload UI;
- status/API endpoints;
- PO download endpoints.

The web layer must not run the full Monday optimizer inside an HTTP request.

### Scheduled Deployments
Separate plain Python entry points run:
- live catalog reconciliation;
- historical sales backfill / incremental refresh;
- nightly inventory snapshots;
- Monday procurement run;
- backup/export jobs;
- monthly pricing transition jobs.

Every scheduled job must become:
- idempotent;
- run-ID keyed;
- protected by a PostgreSQL advisory lock before overlapping schedules are enabled;
- heartbeat/audit logged.

### PostgreSQL
Production structured source of truth for Procurement OS.

Development and production credentials remain separate. Coding agents must never be pointed at production database credentials.

### App Storage
Stores:
- raw supplier books;
- normalized import artifacts;
- parser fixtures;
- generated Shopify PO CSVs;
- Emergency Packets;
- fast on-platform logical backups.

### Off-platform disaster copy
A tiny encrypted backup copy outside Replit is the one intentional infrastructure exception. It is disaster recovery, not an operational dependency.

### GitHub
The repository mirror is the portable source of truth for code/migrations/tests. Procurement domain modules must not import Replit-specific SDKs.
