from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compose_caddy_canonicalizes_mcp_oauth_client_ip() -> None:
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    oauth_routes = caddyfile.split("@mcp_oauth_routes", maxsplit=1)[1].split(
        "reverse_proxy http://ui:3000", maxsplit=1
    )[0]

    for path in (
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource",
        "/authorize",
        "/token",
        "/register",
        "/consent",
        "/auth/callback",
    ):
        assert path in oauth_routes
    assert "trusted_proxies_strict" in caddyfile
    assert "header_up X-Forwarded-For {client_ip}" in oauth_routes
    assert "header_up X-Real-IP {client_ip}" in oauth_routes


def _upstream_block(config: str, upstream: str) -> str:
    """Return the header_up lines of a Caddyfile reverse_proxy block."""
    after = config.split(upstream, maxsplit=1)[1]
    lines = []
    for line in after.splitlines()[1:]:
        if line.strip() == "}":
            break
        lines.append(line.strip())
    return "\n".join(lines)


def test_api_upstream_forwards_host_and_proto_for_oidc_discovery() -> None:
    """The MCP OIDC discovery endpoint builds advertised endpoint URLs from
    X-Forwarded-Host/Proto, so the api upstream must forward both."""
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    api_upstream = _upstream_block(caddyfile, "reverse_proxy http://api:8000")
    assert "header_up X-Forwarded-Host {host}" in api_upstream
    assert "header_up X-Forwarded-Proto {scheme}" in api_upstream

    fargate_config = (
        REPO_ROOT / "deployments/fargate/modules/ecs/ecs-caddy.tf"
    ).read_text()
    fargate_api_upstream = _upstream_block(
        fargate_config, "reverse_proxy http://api-service:8000"
    )
    assert "header_up X-Forwarded-Host {host}" in fargate_api_upstream
    assert "header_up X-Forwarded-Proto {scheme}" in fargate_api_upstream


def test_fargate_caddy_trusts_only_alb_subnets() -> None:
    caddy_config = (
        REPO_ROOT / "deployments/fargate/modules/ecs/ecs-caddy.tf"
    ).read_text()

    assert (
        'trusted_proxies static ${join(" ", var.public_subnet_cidrs)}' in caddy_config
    )
    assert "trusted_proxies_strict" in caddy_config
    assert "trusted_proxies static private_ranges" not in caddy_config
