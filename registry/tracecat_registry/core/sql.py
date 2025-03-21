from typing import Annotated, Any, Literal

from typing_extensions import Doc
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine.url import URL
import ipaddress
import re

from tracecat.types.exceptions import TracecatException
from tracecat_registry import RegistrySecret, registry, secrets

sql_secret = RegistrySecret(
    name="sql",
    keys=[
        "SQL_HOST",
        "SQL_PORT",
        "SQL_USER",
        "SQL_PASS",
    ],
)
"""SQL secret.

- name: `sql`
- keys:
    - `SQL_HOST`
    - `SQL_PORT`
    - `SQL_USER`
    - `SQL_PASS`
"""

# Maximum SQL statement length
MAX_SQL_LENGTH = 10000

# Valid database drivers
# Map SQL dialects to their SQLAlchemy drivers
VALID_DATABASE_DRIVERS = {
    "postgresql": "postgresql+psycopg",  # Using psycopg3 driver
    # "mysql": "mysql+pymysql",  # Using PyMySQL driver
    # "sqlite": "sqlite",  # SQLite uses built-in driver
    # "mssql": "mssql+pyodbc",  # Using pyodbc driver
}

# Valid SSL modes
VALID_SSL_MODES: set[str] = {
    "disable",
    "allow",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}


def _is_select_query(query: str) -> bool:
    """
    Check if a query is a SELECT statement.

    This function removes comments and whitespace, then checks if the query
    starts with SELECT. It performs basic validation, not full SQL parsing.

    Args:
        query: The SQL query to check

    Returns:
        bool: True if the query appears to be a SELECT statement
    """
    if not query or not isinstance(query, str):
        return False

    # Strip comments and normalize whitespace
    # Remove block comments
    clean_query = re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)
    # Remove line comments
    clean_query = re.sub(r"--.*?$", " ", clean_query, flags=re.MULTILINE)
    # Normalize whitespace
    clean_query = re.sub(r"\s+", " ", clean_query).strip().lower()

    # Split on common SQL statement separators
    for separator in (";", r"\g", r"\G", "GO"):
        if separator in clean_query:
            clean_query = clean_query.split(separator)[0]

    clean_query = clean_query.strip().lower()

    # Check if it starts with SELECT
    return clean_query.startswith("select ")


def _validate_db_driver(db_driver: str) -> None:
    """
    Validate that the database driver is allowed.

    Args:
        db_driver: The database driver to validate

    Raises:
        ValueError: If the driver is not in the allowed list
    """
    # Extract base driver (remove any +driver suffix)
    base_driver = db_driver.split("+")[0] if "+" in db_driver else db_driver

    if base_driver not in VALID_DATABASE_DRIVERS:
        raise ValueError(
            f"Invalid database driver. Must be one of: {', '.join(VALID_DATABASE_DRIVERS)}"
        )


def _build_connection_url(
    db_driver: str,
    db_name: str | None = None,
    ssl_mode: str | None = None,
) -> URL:
    """
    Build a connection URL for SQLAlchemy.

    Args:
        db_driver: Database driver name
        db_name: Database name
        ssl_mode: SSL mode for connection

    Returns:
        URL: SQLAlchemy URL object

    Raises:
        ValueError: If any validation fails
    """
    # Validate the database driver
    _validate_db_driver(db_driver)

    # Get credentials from registry secrets
    host = secrets.get("SQL_HOST")
    port = secrets.get("SQL_PORT")
    user = secrets.get("SQL_USER")
    password = secrets.get("SQL_PASS")

    if not all((host, port, user, password)):
        raise ValueError("Missing required SQL credentials")

    # Sanitize host to prevent security issues
    if host and isinstance(host, str):
        # Blocked hostnames (lowercase for case-insensitive comparison)
        blocked_hosts = {
            "localhost",
            "localhost.localdomain",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "0:0:0:0:0:0:0:1",
            "0:0:0:0:0:0:0:0",
        }

        # Check for exact hostname matches
        if host.lower() in blocked_hosts:
            raise ValueError(f"Access to host {host} is not allowed: blocked hostname")

        # Block .localhost TLD (RFC 6761)
        if host.lower().endswith(".localhost"):
            raise ValueError("Access to .localhost domains is not allowed")

        # Using ipaddress module to check if IP is in a blocked range
        try:
            ip = ipaddress.ip_address(host)

            # Check various problematic IP types
            if (
                ip.is_loopback  # 127.0.0.0/8, ::1
                or ip.is_unspecified  # 0.0.0.0, ::
                or ip.is_link_local  # 169.254.0.0/16, fe80::/10
                or ip.is_multicast  # 224.0.0.0/4, ff00::/8
                or ip.is_private
            ):  # 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7
                raise ValueError(
                    f"Access to address {host} is not allowed: restricted IP address type"
                )

            # Additional check for IPv4-mapped IPv6 addresses
            if ip.version == 6 and hasattr(ip, "ipv4_mapped") and ip.ipv4_mapped:
                ipv4 = ip.ipv4_mapped
                if ipv4.is_loopback or ipv4.is_private or ipv4.is_unspecified:
                    raise ValueError(
                        f"Access to mapped IPv4 address {ipv4} is not allowed"
                    )

        except ValueError as e:
            # If the error came from our validation, re-raise it
            if "is not allowed" in str(e):
                raise

        # Valid hostname pattern
        if host == "postgres_db":
            raise ValueError("Cannot use reserved hostname 'postgres_db'")

        # NOTE: Hostnames should not contain underscores
        host_pattern = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]{0,253}[a-zA-Z0-9])?$")
        if not host_pattern.match(host):
            raise ValueError("Invalid host format")

    # Add query parameters if needed
    kwargs = {}
    if port is not None:
        # Ensure port is a valid integer
        if not port.isdigit():
            raise ValueError("Port must be a number")
        kwargs["port"] = int(port)
        if not (1 <= kwargs["port"] <= 65535):
            raise ValueError("Port must be between 1 and 65535")

    # Create query parameters
    query_params = {}

    # Add SSL mode if specified
    if ssl_mode:
        if ssl_mode not in VALID_SSL_MODES:
            raise ValueError(
                f"Invalid SSL mode. Must be one of: {', '.join(VALID_SSL_MODES)}"
            )
        query_params["sslmode"] = ssl_mode

    # Add query parameters if we have any
    if query_params:
        kwargs["query"] = query_params

    return URL.create(
        drivername=VALID_DATABASE_DRIVERS[db_driver],
        username=user,
        password=password,
        host=host,
        database=db_name,
        **kwargs,
    )


def _get_engine(connection_url: URL, timeout: int = 30) -> Engine:
    """
    Get a read-only SQLAlchemy engine with security settings.

    Args:
        connection_url: SQLAlchemy URL object
        timeout: Query execution timeout in seconds

    Returns:
        Engine: SQLAlchemy engine
    """
    connect_args: dict[str, Any] = {}
    execution_options: dict[str, Any] = {}

    # Add driver-specific timeout settings
    driver = str(connection_url).split("://")[0]
    if not driver.startswith("postgresql"):
        raise ValueError(
            f"Driver not supported. Must be one of: {', '.join(VALID_DATABASE_DRIVERS.keys())}"
        )
    if not isinstance(timeout, int):
        raise ValueError("Timeout must be an integer")
    connect_args["connect_timeout"] = timeout
    connect_args["options"] = "-c statement_timeout={}s".format(timeout)

    # XXX(security): This is a temporary solution to prevent SQL injection and dangerous queries.
    execution_options["postgresql_readonly"] = True

    return create_engine(
        connection_url,
        future=True,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
        execution_options=execution_options,
    )


@registry.register(
    namespace="core.sql",
    description="Query a database with a SELECT statement. Returns a list of results.",
    default_title="Query Database",
    secrets=[sql_secret],
)
def query(
    statement: Annotated[
        str,
        Doc("SQL SELECT query to execute. Only SELECT statements are allowed."),
    ],
    db_driver: Annotated[
        Literal["postgresql"],
        Doc("Database driver. Currently only PostgreSQL is supported."),
    ] = "postgresql",
    db_name: Annotated[
        str | None,
        Doc("Database name. If not provided, the default database will be used."),
    ] = None,
    ssl_mode: Annotated[
        str | None,
        Doc("SSL mode for connection (e.g., 'require', 'disable', etc.)"),
    ] = None,
    params: Annotated[
        dict[str, Any] | None,
        Doc("Parameters to bind to the query for SQL injection protection"),
    ] = None,
    use_transaction: Annotated[
        bool,
        Doc("Whether to execute the query within a transaction"),
    ] = True,
    timeout: Annotated[
        int,
        Doc("Query timeout in seconds"),
    ] = 30,
) -> list[dict[str, Any]]:
    """
    Execute a read-only SQL SELECT query against a database using SQLAlchemy.

    This function enforces security by only allowing SELECT statements and
    using parameter binding to prevent SQL injection attacks.

    Args:
        statement: SQL SELECT query to execute
        db_driver: Type of database (postgresql, mysql, etc.)
        db_name: Name of the database to connect to
        ssl_mode: SSL mode for the connection
        params: Parameters to bind to the query for SQL injection protection
        use_transaction: Whether to execute the query within a transaction
        timeout: Query timeout in seconds

    Returns:
        List of dictionaries representing query results

    Raises:
        ValueError: If the query is not a SELECT statement or exceeds length limit
        Exception: If there is an error connecting to the database or executing the query
    """
    # Validate query length
    if len(statement) > MAX_SQL_LENGTH:
        raise ValueError(f"SQL statement too long (max {MAX_SQL_LENGTH} characters)")

    # Validate query type - only allow SELECT statements
    if not _is_select_query(statement):
        raise ValueError("Only SELECT queries are allowed for security reasons")

    # Build connection URL with read-only mode enabled
    connection_url = _build_connection_url(
        db_driver=db_driver,
        db_name=db_name,
        ssl_mode=ssl_mode,
    )

    # Get engine with timeout
    engine = _get_engine(connection_url, timeout=timeout)

    try:
        # Execute query using SQLAlchemy's text construct with parameter binding
        with engine.connect() as conn:
            # Create text object for query
            query_obj = text(statement)

            # Execute with or without transaction based on parameter
            if use_transaction:
                with conn.begin():
                    # Use parameters for SQL injection protection
                    result = conn.execute(query_obj, parameters=params or {})
            else:
                # Use parameters for SQL injection protection
                result = conn.execute(query_obj, parameters=params or {})

            # Convert results to list of dictionaries
            column_names = result.keys()
            results = [dict(zip(column_names, row)) for row in result.fetchall()]

        return results

    except Exception as e:
        raise TracecatException(f"Error executing query: {str(e)}")
