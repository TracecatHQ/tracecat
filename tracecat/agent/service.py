"""Agent management service — REMOVED.

This monolith has been decomposed into domain-specific submodules:

  catalog     tracecat.agent.catalog.service      (AgentCatalogService, AdminAgentCatalogService)
  credentials tracecat.agent.credentials.service   (AgentCredentialsService)
  selections  tracecat.agent.selections.service    (AgentSelectionsService)
  sources     tracecat.agent.sources.service       (AgentSourceService)
  runtime     tracecat.agent.runtime.service       (AgentRuntimeService)
  startup     tracecat.agent.catalog.startup       (sync_model_catalogs_on_startup)
"""

raise ImportError(
    "tracecat.agent.service has been removed. "
    "Import from the domain submodules listed in this module's docstring."
)
