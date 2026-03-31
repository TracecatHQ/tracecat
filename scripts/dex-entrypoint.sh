#!/bin/sh
set -eu

template_path="/etc/dex/config.docker.yaml"
rendered_path="/tmp/dex-config.yaml"

set_default_mcp_auth_mode() {
  [ -n "${MCP_AUTH_MODE-}" ] && return 0

  auth_types=$(printf '%s' "${TRACECAT__AUTH_TYPES-basic}" | tr '[:upper:]' '[:lower:]')
  auth_types=$(printf ',%s,' "$auth_types" | tr -d '[:space:]')

  case "$auth_types" in
    *,oidc,*)
      if [ -n "${OIDC_ISSUER-}" ] \
        && [ -n "${OIDC_CLIENT_ID-}" ] \
        && [ -n "${OIDC_CLIENT_SECRET-}" ]; then
        export MCP_AUTH_MODE="oidc"
        return 0
      fi
      ;;
  esac

  case "$auth_types" in
    *,basic,*)
      export MCP_AUTH_MODE="basic"
      ;;
  esac
}

set_default_port_redirect_alias() {
  value="${DEX_TRACECAT_MCP_REDIRECT_URI-}"
  [ -n "$value" ] || return 0
  case "$value" in
    http://*:80/*)
      export DEX_TRACECAT_MCP_REDIRECT_URI_ALT="$(printf '%s' "$value" | sed 's|:80/|/|')"
      ;;
    https://*:443/*)
      export DEX_TRACECAT_MCP_REDIRECT_URI_ALT="$(printf '%s' "$value" | sed 's|:443/|/|')"
      ;;
  esac
}

set_upstream_oidc_scopes_yaml() {
  scopes=$(printf '%s' "${MCP_DEX_OIDC_SCOPES:-${OIDC_SCOPES:-openid profile email}}" | tr ',' ' ')
  scopes=$(printf '%s\n' "$scopes" | awk '{$1=$1; print}')
  [ -n "$scopes" ] || scopes="openid profile email"

  yaml_lines=""
  for scope in $scopes; do
    yaml_lines="${yaml_lines}        - ${scope}
"
  done

  export DEX_UPSTREAM_OIDC_SCOPES_YAML="$yaml_lines"
}

set_default_mcp_auth_mode
set_default_port_redirect_alias
set_upstream_oidc_scopes_yaml

gomplate -f "$template_path" -o "$rendered_path"

exec dex serve "$rendered_path"
