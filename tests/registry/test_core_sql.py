from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import SQLAlchemyError
from tracecat_registry.core.sql import (
    MAX_SQL_LENGTH,
    VALID_DATABASE_DRIVERS,
    _build_connection_url,
    _is_select_query,
    _validate_db_driver,
    query,
)


class TestSelectQueryValidator:
    """Tests for the _is_select_query function."""

    @pytest.mark.parametrize(
        "query_text,expected",
        [
            # Valid SELECT queries
            ("SELECT * FROM users", True),
            ("select id, name from users", True),
            ("  SELECT  *  FROM  users  ", True),
            ("/* comment */ SELECT id FROM users", True),
            ("-- comment\nSELECT id FROM users", True),
            # Invalid queries
            ("INSERT INTO users VALUES (1, 'test')", False),
            ("UPDATE users SET name='test'", False),
            ("DELETE FROM users", False),
            ("DROP TABLE users", False),
            ("CREATE TABLE users (id INT)", False),
            ("TRUNCATE TABLE users", False),
            ("", False),
            (None, False),
            (123, False),  # Non-string
        ],
    )
    def test_select_query_validation(self, query_text, expected):
        """Test SELECT query validation function."""
        assert _is_select_query(query_text) == expected


class TestDriverValidator:
    """Tests for the _validate_db_driver function."""

    @pytest.mark.parametrize("driver", list(VALID_DATABASE_DRIVERS))
    def test_valid_drivers(self, driver):
        """Test with valid database drivers."""
        # Should not raise an exception
        _validate_db_driver(driver)

        # Test with driver variants
        _validate_db_driver(f"{driver}+somedriver")

    @pytest.mark.parametrize(
        "driver",
        [
            "invalid",
            "mongodb",  # Not in our valid drivers
            "redis",
            "cassandra",
            "",
        ],
    )
    def test_invalid_drivers(self, driver):
        """Test with invalid database drivers."""
        with pytest.raises(ValueError, match="Invalid database driver"):
            _validate_db_driver(driver)


class TestBuildConnectionUrl:
    """Tests for the _build_connection_url function."""

    @pytest.fixture
    def mock_secrets(self):
        """Mock the secrets.get function to return test values."""
        with patch("tracecat_registry.core.sql.secrets.get") as mock_get:
            # Set up default valid values
            def side_effect(key):
                return {
                    "SQL_HOST": "example.com",
                    "SQL_PORT": "5432",
                    "SQL_USER": "testuser",
                    "SQL_PASS": "testpass",
                }[key]

            mock_get.side_effect = side_effect
            yield mock_get

    def test_valid_host(self, mock_secrets):
        """Test with a valid hostname."""
        url = _build_connection_url("postgresql", "testdb", None)
        assert isinstance(url, URL)
        assert url.host == "example.com"
        assert url.database == "testdb"
        assert url.username == "testuser"
        assert url.password == "testpass"
        assert url.drivername == "postgresql+psycopg"

    def test_missing_credentials(self, mock_secrets):
        """Test with missing credentials."""
        mock_secrets.side_effect = lambda key: None if key == "SQL_USER" else "value"
        with pytest.raises(ValueError, match="Missing required SQL credentials"):
            _build_connection_url("postgresql", "testdb", None)

    def test_port_conversion(self, mock_secrets):
        """Test port is converted to integer."""
        url = _build_connection_url("postgresql", "testdb", None)
        assert url.port == 5432

    def test_invalid_port(self, mock_secrets):
        """Test with invalid port format."""
        mock_secrets.side_effect = (
            lambda key: "not_a_number" if key == "SQL_PORT" else "value"
        )
        with pytest.raises(ValueError, match="Port must be a number"):
            _build_connection_url("postgresql", "testdb", None)

    def test_ssl_mode(self, mock_secrets):
        """Test with SSL mode parameter."""
        url = _build_connection_url("postgresql", "testdb", "require")
        assert url.query["sslmode"] == "require"

    def test_invalid_ssl_mode(self, mock_secrets):
        """Test with invalid SSL mode."""
        with pytest.raises(ValueError, match="Invalid SSL mode"):
            _build_connection_url("postgresql", "testdb", "invalid_mode")

    @pytest.mark.parametrize(
        "blocked_host",
        [
            "localhost",
            "localhost.localdomain",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            "0:0:0:0:0:0:0:1",
            "0:0:0:0:0:0:0:0",
            "sub.localhost",  # Test .localhost TLD
        ],
    )
    def test_blocked_hostnames(self, mock_secrets, blocked_host):
        """Test with blocked hostnames."""
        mock_secrets.side_effect = (
            lambda key: blocked_host if key == "SQL_HOST" else "value"
        )
        with pytest.raises(ValueError, match="is not allowed"):
            _build_connection_url("postgresql", "testdb", None)

    @pytest.mark.parametrize(
        "private_ip",
        [
            "10.0.0.1",  # Private IPv4
            "172.16.0.1",  # Private IPv4
            "192.168.0.1",  # Private IPv4
            "169.254.0.1",  # Link-local IPv4
            "224.0.0.1",  # Multicast IPv4
            "fc00::1",  # Private IPv6
            "fe80::1",  # Link-local IPv6
            "ff00::1",  # Multicast IPv6
            "::ffff:127.0.0.1",  # IPv4-mapped IPv6 loopback
            "::ffff:10.0.0.1",  # IPv4-mapped IPv6 private
        ],
    )
    def test_blocked_ip_ranges(self, mock_secrets, private_ip):
        """Test with IPs in blocked ranges."""
        mock_secrets.side_effect = (
            lambda key: private_ip if key == "SQL_HOST" else "value"
        )
        with pytest.raises(ValueError, match="is not allowed"):
            _build_connection_url("postgresql", "testdb", None)

    def test_invalid_hostname_format(self, mock_secrets):
        """Test with invalid hostname format."""
        mock_secrets.side_effect = (
            lambda key: "invalid-host!" if key == "SQL_HOST" else "value"
        )
        with pytest.raises(ValueError, match="Invalid host format"):
            _build_connection_url("postgresql", "testdb", None)

    @pytest.mark.parametrize(
        "invalid_driver", ["mongodb", "redis", "cassandra", "neo4j", "elasticsearch"]
    )
    def test_invalid_db_driver(self, mock_secrets, invalid_driver):
        """Test with invalid database driver."""
        with pytest.raises(ValueError, match="Invalid database driver"):
            _build_connection_url(invalid_driver, "testdb", None)


class TestQuery:
    """Tests for the query function."""

    @pytest.fixture
    def mock_engine(self):
        """Mock SQLAlchemy engine creation."""
        with patch("tracecat_registry.core.sql._get_engine") as mock_get_engine:
            engine_mock = MagicMock()
            conn_mock = MagicMock()
            result_mock = MagicMock()

            # Configure mocks for the connection flow
            engine_mock.connect.return_value.__enter__.return_value = conn_mock
            conn_mock.execute.return_value = result_mock
            result_mock.keys.return_value = ["id", "name"]
            result_mock.fetchall.return_value = [(1, "test1"), (2, "test2")]

            mock_get_engine.return_value = engine_mock
            yield mock_get_engine, conn_mock

    @pytest.fixture
    def mock_build_url(self):
        """Mock the _build_connection_url function."""
        with patch("tracecat_registry.core.sql._build_connection_url") as mock:
            mock.return_value = "mock_url"
            yield mock

    @pytest.fixture
    def mock_is_select(self):
        """Mock the _is_select_query function to return True."""
        with patch("tracecat_registry.core.sql._is_select_query") as mock:
            mock.return_value = True
            yield mock

    def test_query_execution(self, mock_engine, mock_build_url, mock_is_select):
        """Test basic query execution."""
        mock_get_engine, conn_mock = mock_engine

        result = query(
            "SELECT * FROM test", "postgresql", "testdb", None, {"param1": "value1"}
        )

        # Verify URL was built with correct parameters
        mock_build_url.assert_called_once_with(
            db_driver="postgresql", db_name="testdb", ssl_mode=None
        )

        # Verify query was executed
        engine = mock_get_engine.return_value
        conn = engine.connect.return_value.__enter__.return_value
        conn.execute.assert_called_once()

        # Verify parameters were passed
        args, kwargs = conn.execute.call_args
        assert kwargs["parameters"] == {"param1": "value1"}

        # Verify result formatting
        assert result == [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]

    def test_query_length_validation(self, mock_engine, mock_build_url):
        """Test query length validation."""
        with pytest.raises(ValueError, match="SQL statement too long"):
            query("SELECT * FROM test" + "x" * MAX_SQL_LENGTH, "postgresql", "testdb")

    def test_query_type_validation(self, mock_engine, mock_build_url):
        """Test query type validation."""
        with patch("tracecat_registry.core.sql._is_select_query") as mock_is_select:
            mock_is_select.return_value = False
            with pytest.raises(ValueError, match="Only SELECT queries are allowed"):
                query("INSERT INTO test VALUES (1)", "postgresql", "testdb")

    def test_query_sqlalchemy_error(self, mock_engine, mock_build_url, mock_is_select):
        """Test handling of SQLAlchemy errors."""
        mock_get_engine, conn_mock = mock_engine
        conn_mock.execute.side_effect = SQLAlchemyError("Database error")

        with pytest.raises(Exception, match="Database error"):
            query("SELECT * FROM test", "postgresql", "testdb", None)

    def test_query_general_error(self, mock_engine, mock_build_url, mock_is_select):
        """Test handling of general errors."""
        mock_get_engine, conn_mock = mock_engine
        conn_mock.execute.side_effect = Exception("General error")

        with pytest.raises(Exception, match="Error executing query"):
            query("SELECT * FROM test", "postgresql", "testdb", None)

    @pytest.mark.parametrize("use_transaction", [True, False])
    def test_transaction_handling(
        self, mock_engine, mock_build_url, mock_is_select, use_transaction
    ):
        """Test query with transaction handling."""
        mock_get_engine, conn_mock = mock_engine

        result = query(
            "SELECT * FROM test",
            "postgresql",
            "testdb",
            None,
            use_transaction=use_transaction,
        )

        # Verify result was returned correctly
        assert result == [{"id": 1, "name": "test1"}, {"id": 2, "name": "test2"}]

    def test_query_timeout(self, mock_engine, mock_build_url, mock_is_select):
        """Test query timeout parameter is passed to engine creation."""
        mock_get_engine, conn_mock = mock_engine

        query("SELECT * FROM test", "postgresql", "testdb", None, timeout=60)

        # Verify timeout was passed to _get_engine
        mock_get_engine.assert_called_once_with("mock_url", timeout=60)
