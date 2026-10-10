#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres \
  -v afpass="$AIRFLOW_DB_PASSWORD" -v whpass="$WAREHOUSE_PASSWORD" \
  -v mbpass="$METABASE_DB_PASSWORD" -v bipass="$BI_PASSWORD" <<'SQL'
CREATE ROLE airflow LOGIN PASSWORD :'afpass';
CREATE ROLE warehouse LOGIN PASSWORD :'whpass';
CREATE ROLE metabase LOGIN PASSWORD :'mbpass';
CREATE ROLE bi_reader LOGIN PASSWORD :'bipass';
CREATE DATABASE airflow OWNER airflow;
CREATE DATABASE warehouse OWNER warehouse;
CREATE DATABASE metabase OWNER metabase;
REVOKE ALL ON DATABASE warehouse FROM PUBLIC;
GRANT CONNECT ON DATABASE warehouse TO warehouse,bi_reader;
SQL
psql -v ON_ERROR_STOP=1 --username postgres --dbname warehouse <<'SQL'
SET ROLE warehouse;
\i /lab-sql/01_warehouse.sql
GRANT USAGE ON SCHEMA cdm TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA cdm TO bi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA cdm GRANT SELECT ON TABLES TO bi_reader;
SQL
