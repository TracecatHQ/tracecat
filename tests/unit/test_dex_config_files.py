from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "docker-compose.local.yml",
)
BASE_DOMAIN_EXPR = "BASE_DOMAIN=${BASE_DOMAIN:-:${PUBLIC_APP_PORT:-80}}"
DEX_BASE_DOMAIN_EXPR = (
    "DEX_BASE_DOMAIN=${DEX_BASE_DOMAIN:-http://dex.localhost:${PUBLIC_APP_PORT:-80}}"
)
DEX_ISSUER_EXPR = (
    "DEX_ISSUER: ${DEX_ISSUER:-${TRACECAT__PUBLIC_APP_URL:"
    "-${PUBLIC_APP_URL:-${PUBLIC_URL:-http://localhost:${PUBLIC_APP_PORT:-80}}}}/auth}"
)
DEX_UPSTREAM_REDIRECT_EXPR = (
    "DEX_UPSTREAM_REDIRECT_URI: ${DEX_UPSTREAM_REDIRECT_URI:"
    "-${TRACECAT__PUBLIC_APP_URL:-${PUBLIC_APP_URL:"
    "-${PUBLIC_URL:-http://localhost:${PUBLIC_APP_PORT:-80}}}}/auth/callback}"
)


def test_compose_dex_urls_fall_back_to_public_app_url() -> None:
    for relative_path in COMPOSE_FILES:
        contents = (REPO_ROOT / relative_path).read_text()
        assert BASE_DOMAIN_EXPR in contents
        assert DEX_BASE_DOMAIN_EXPR in contents
        assert DEX_ISSUER_EXPR in contents
        assert DEX_UPSTREAM_REDIRECT_EXPR in contents


def test_dex_oidc_connector_uses_configured_scopes() -> None:
    template = (REPO_ROOT / "deployments/dex/config.docker.yaml").read_text()
    entrypoint = (REPO_ROOT / "scripts/dex-entrypoint.sh").read_text()
    env_example = (REPO_ROOT / ".env.example").read_text()

    assert "{{ .Env.DEX_UPSTREAM_OIDC_SCOPES_YAML }}" in template
    assert 'index .Env "DEX_TRACECAT_MCP_REDIRECT_URI_ALT"' in template
    assert "MCP_DEX_OIDC_SCOPES" in entrypoint
    assert "set_upstream_oidc_scopes_yaml" in entrypoint
    assert ".Env.MCP_AUTH_MODE" in template
    assert "MCP_AUTH_MODE" in entrypoint
    assert 'OIDC_SCOPES="openid profile email"' in env_example
    assert 'MCP_AUTH_MODE="oidc"' in env_example
    assert 'MCP_DEX_OIDC_SCOPES="openid profile email offline_access"' in env_example


def test_compose_files_wire_mcp_auth_mode_into_caddy_and_dex() -> None:
    for relative_path in COMPOSE_FILES:
        contents = (REPO_ROOT / relative_path).read_text()
        assert "MCP_AUTH_MODE:" in contents
        assert "- MCP_AUTH_MODE=${MCP_AUTH_MODE:-}" in contents


def test_caddy_routes_dex_assets_under_auth_prefix_only() -> None:
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()

    assert "handle /auth/static/*" in caddyfile
    assert "handle /auth/theme/*" in caddyfile
    assert "handle /static/*" not in caddyfile
    assert "handle /theme/*" not in caddyfile
    assert 'expression "{$MCP_AUTH_MODE}" == "saml"' in caddyfile
    assert "redir /sign-in 302" in caddyfile
