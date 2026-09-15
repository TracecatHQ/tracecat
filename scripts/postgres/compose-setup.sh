#!/usr/bin/env bash
# Use the same database URI as migrations. Only the bundled server is provisioned;
# external databases are checked using the application role's existing permissions.
set -euo pipefail
: "${TRACECAT__DB_URI:?TRACECAT__DB_URI must match the migration database}"
uri=$TRACECAT__DB_URI
# SQLAlchemy's driver suffix is not part of libpq's URI syntax. Preserve the rest,
# including the database name, escaped credentials, and SSL/query options.
uri=${uri/#postgresql+psycopg:\/\//postgresql:\/\/}
uri=${uri/#postgresql+asyncpg:\/\//postgresql:\/\/}
export PGCONNECT_TIMEOUT=5
deadline=$((SECONDS + 180))
server=''
while ((SECONDS < deadline)); do
    if server=$(psql -X --dbname "$uri" -At -v ON_ERROR_STOP=1 \
        -c "SELECT host(inet_server_addr()) || '|' || inet_server_port()" 2>/dev/null); then
        break
    fi
    sleep 2
done
if [[ -z "$server" ]]; then
    echo 'Cannot connect to the migration database within 180 seconds; check its availability and TRACECAT__DB_URI.' >&2
    exit 1
fi

# Compare the connected server, not text in the URI: query parameters can override
# the URI hostname. Never attempt privileged installation on an external server.
install=false
while read -r address _; do
    if [[ "$server" == "$address|5432" ]]; then
        install=true
        break
    fi
done < <(getent ahosts postgres_db || true)
if [[ "$install" == true ]]; then
    echo 'Provisioning pgvector in the bundled application database.'
    database=$(psql -X --dbname "$uri" -At -v ON_ERROR_STOP=1 -c 'SELECT current_database()')
    # Use the existing bundled administrator only after identifying the server.
    # PGDATABASE is a literal database name, so unusual names cannot become a URI.
    PGHOST=postgres_db PGPORT=5432 PGDATABASE="$database" \
        PGUSER="$TRACECAT__POSTGRES_USER" PGPASSWORD="$TRACECAT__POSTGRES_PASSWORD" \
        psql -X -v install=true -f /pgvector.sql
else
    echo 'Checking pgvector in the external application database (administrator provisioning required).'
fi
# Validate again as the migration role, including its type/operator permissions.
exec psql -X --dbname "$uri" -f /pgvector.sql
