-- Canonical empty-database initializer. Run with psql so \ir is resolved
-- relative to this file. It is additive and does not remove public legacy data.
\set ON_ERROR_STOP on
\ir v2_schema.sql
\ir v2_seed.sql

