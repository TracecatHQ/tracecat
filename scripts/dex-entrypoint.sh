#!/bin/sh
set -eu

template_path="/etc/dex/config.docker.yaml"
rendered_path="/tmp/dex-config.yaml"

normalize_cert_var() {
  var_name="$1"
  eval "value=\${$var_name-}"
  [ -n "$value" ] || return 0
  case "$value" in
    *"BEGIN CERTIFICATE"*) return 0 ;;
  esac

  wrapped=$(printf '%s\n%s\n%s' \
    '-----BEGIN CERTIFICATE-----' \
    "$value" \
    '-----END CERTIFICATE-----')
  export "$var_name=$wrapped"
}

set_saml_ca_data_b64() {
  source_var="$1"
  eval "value=\${$source_var-}"
  [ -n "$value" ] || return 0
  export DEX_SAML_CA_DATA_B64="$(printf '%s' "$value" | base64 | tr -d '\n')"
}

set_default_mcp_dex_mode() {
  [ -n "${MCP_DEX_MODE-}" ] && return 0

  auth_types=$(printf '%s' "${TRACECAT__AUTH_TYPES-basic}" | tr '[:upper:]' '[:lower:]')
  auth_types=$(printf ',%s,' "$auth_types" | tr -d '[:space:]')

  case "$auth_types" in
    *,saml,*)
      if [ -n "${SAML_IDP_METADATA_URL-}" ] \
        && [ -n "${MCP_DEX_SAML_SSO_URL-}" ] \
        && { [ -n "${MCP_DEX_SAML_CA_DATA-}" ] || [ -n "${SAML_METADATA_CERT-}" ]; }; then
        export MCP_DEX_MODE="saml"
        return 0
      fi
      ;;
  esac

  case "$auth_types" in
    *,oidc,*)
      if [ -n "${OIDC_ISSUER-}" ] \
        && [ -n "${OIDC_CLIENT_ID-}" ] \
        && [ -n "${OIDC_CLIENT_SECRET-}" ]; then
        export MCP_DEX_MODE="oidc"
        return 0
      fi
      ;;
  esac

  case "$auth_types" in
    *,basic,*)
      export MCP_DEX_MODE="basic"
      ;;
  esac
}

set_preferred_saml_ca_data_b64() {
  if [ -n "${MCP_DEX_SAML_CA_DATA-}" ]; then
    set_saml_ca_data_b64 MCP_DEX_SAML_CA_DATA
    return 0
  fi
  if [ -n "${SAML_METADATA_CERT-}" ]; then
    set_saml_ca_data_b64 SAML_METADATA_CERT
  fi
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

normalize_cert_var MCP_DEX_SAML_CA_DATA
normalize_cert_var SAML_METADATA_CERT
set_default_mcp_dex_mode
set_preferred_saml_ca_data_b64
set_default_port_redirect_alias

gomplate -f "$template_path" -o "$rendered_path"

exec dex serve "$rendered_path"
