"""LiteLLM Gateway for multi-tenant LLM access.

This module implements a LiteLLM proxy with custom authentication that resolves
credentials per-request based on workspace_id. The gateway runs as a separate
Docker service and provides an OpenAI-compatible API.

Architecture:
- LiteLLM runs as an OpenAI-compatible proxy
- `user_api_key_auth()` validates service key + extracts workspace_id
- `async_pre_call_hook()` fetches credentials from DB and injects into request
- Credentials never leave the gateway except to LLM providers
"""
