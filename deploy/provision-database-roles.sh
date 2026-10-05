#!/bin/sh
# Create login roles and reapply least-privilege grants after every schema migration.
# Passwords enter this short-lived container only through read-only secret files.
set -eu

read_secret() {
    secret_path=$1
    secret_label=$2
    if [ ! -r "$secret_path" ]; then
        echo "$secret_label secret file is unavailable" >&2
        exit 1
    fi
    secret_value=$(cat "$secret_path")
    if [ -z "$secret_value" ]; then
        echo "$secret_label secret file is empty" >&2
        exit 1
    fi
    case "$secret_value" in
        *"
"*)
            echo "$secret_label secret contains an embedded newline" >&2
            exit 1
            ;;
    esac
    printf '%s' "$secret_value"
}

if [ "$POSTGRES_RUNTIME_USER" = "$POSTGRES_MIGRATION_USER" ] || \
   [ "$POSTGRES_RUNTIME_USER" = "$POSTGRES_GOVERNANCE_USER" ] || \
   [ "$POSTGRES_MIGRATION_USER" = "$POSTGRES_GOVERNANCE_USER" ]; then
    echo "database role names must be distinct" >&2
    exit 1
fi

PNW_MIGRATION_PASSWORD=$(read_secret /run/secrets/postgres_migration_password migration)
PNW_RUNTIME_PASSWORD=$(read_secret /run/secrets/postgres_runtime_password runtime)
PNW_GOVERNANCE_PASSWORD=$(read_secret /run/secrets/postgres_governance_password governance)
export PGPASSWORD=$PNW_MIGRATION_PASSWORD
export PNW_RUNTIME_PASSWORD PNW_GOVERNANCE_PASSWORD

psql \
    --host="$POSTGRES_HOST" \
    --port="$POSTGRES_PORT" \
    --username="$POSTGRES_MIGRATION_USER" \
    --dbname="$POSTGRES_DB" \
    --no-password \
    --set=ON_ERROR_STOP=1 \
    --set=database_name="$POSTGRES_DB" \
    --set=runtime_role="$POSTGRES_RUNTIME_USER" \
    --set=governance_role="$POSTGRES_GOVERNANCE_USER" <<'SQL'
\getenv runtime_password PNW_RUNTIME_PASSWORD
\getenv governance_password PNW_GOVERNANCE_PASSWORD

SELECT format(
    'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'runtime_role',
    :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'runtime_role')
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'runtime_role',
    :'runtime_password'
)
\gexec

SELECT format(
    'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'governance_role',
    :'governance_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'governance_role')
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'governance_role',
    :'governance_password'
)
\gexec

REVOKE TEMPORARY ON DATABASE :"database_name" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

REVOKE ALL PRIVILEGES ON DATABASE :"database_name" FROM :"runtime_role";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM :"runtime_role";
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM :"runtime_role";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM :"runtime_role";
GRANT CONNECT ON DATABASE :"database_name" TO :"runtime_role";
GRANT USAGE ON SCHEMA public TO :"runtime_role";
GRANT SELECT ON
    sources,
    source_versions,
    source_links,
    qualifications,
    applicability,
    evidence_blocks,
    embeddings,
    course_relations,
    office_referrals,
    conflicts,
    conflict_evidence_blocks
TO :"runtime_role";
GRANT SELECT, INSERT, UPDATE ON aggregate_metrics TO :"runtime_role";
GRANT EXECUTE ON FUNCTION public.lock_sources_for_answer(uuid[]) TO :"runtime_role";

REVOKE ALL PRIVILEGES ON DATABASE :"database_name" FROM :"governance_role";
REVOKE ALL PRIVILEGES ON SCHEMA public FROM :"governance_role";
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM :"governance_role";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM :"governance_role";
GRANT CONNECT ON DATABASE :"database_name" TO :"governance_role";
GRANT USAGE ON SCHEMA public TO :"governance_role";
GRANT SELECT, INSERT, UPDATE ON
    sources,
    source_versions,
    source_links,
    qualifications,
    applicability,
    evidence_blocks,
    embeddings,
    course_relations,
    office_referrals,
    conflicts,
    conflict_evidence_blocks,
    source_events,
    ingestion_runs
TO :"governance_role";
GRANT EXECUTE ON FUNCTION public.lock_sources_for_answer(uuid[]) TO :"governance_role";
SQL

unset PGPASSWORD PNW_MIGRATION_PASSWORD PNW_RUNTIME_PASSWORD PNW_GOVERNANCE_PASSWORD
