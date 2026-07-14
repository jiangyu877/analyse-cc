# Data Retention

Targets are RPO 24 hours, RTO 4 hours, and 99.5% monthly availability. Application logs and database backups are retained 30 days; audit logs are retained 180 days; migration history is permanent. Run a quarterly restore drill, restrict backup ACLs, and keep encrypted off-host copies.
