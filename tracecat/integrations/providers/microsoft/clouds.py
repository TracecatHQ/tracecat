"""Azure cloud environment metadata and helpers for Microsoft integrations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AzureCloud(StrEnum):
    """Supported Azure cloud environments."""

    PUBLIC = "public"
    US_GOV = "us_gov"


@dataclass(frozen=True)
class AzureCloudConfig:
    """Configuration for an Azure sovereign cloud."""

    authority_host: str
    management_resource: str
    log_analytics_resource: str
    graph_resource: str


AZURE_CLOUD_CONFIG: dict[AzureCloud, AzureCloudConfig] = {
    AzureCloud.PUBLIC: AzureCloudConfig(
        authority_host="https://login.microsoftonline.com",
        management_resource="https://management.azure.com",
        log_analytics_resource="https://api.loganalytics.io",
        graph_resource="https://graph.microsoft.com",
    ),
    AzureCloud.US_GOV: AzureCloudConfig(
        authority_host="https://login.microsoftonline.us",
        management_resource="https://management.usgovcloudapi.net",
        log_analytics_resource="https://api.loganalytics.us",
        graph_resource="https://graph.microsoft.us",
    ),
}


def get_authorization_endpoint(
    cloud: AzureCloud, tenant_id: str, *, path: str = "oauth2/v2.0/authorize"
) -> str:
    """Build the authorization endpoint for a tenant in the specified cloud."""
    config = AZURE_CLOUD_CONFIG[cloud]
    return f"{config.authority_host}/{tenant_id}/{path}"


def get_token_endpoint(
    cloud: AzureCloud, tenant_id: str, *, path: str = "oauth2/v2.0/token"
) -> str:
    """Build the token endpoint for a tenant in the specified cloud."""
    config = AZURE_CLOUD_CONFIG[cloud]
    return f"{config.authority_host}/{tenant_id}/{path}"


def _remap_scopes_for_cloud(
    scopes: list[str], cloud: AzureCloud, *, resource_attr: str
) -> list[str]:
    """Remap public-cloud resource scopes to the target cloud."""
    origin_resource = getattr(AZURE_CLOUD_CONFIG[AzureCloud.PUBLIC], resource_attr)
    target_resource = getattr(AZURE_CLOUD_CONFIG[cloud], resource_attr)

    if origin_resource == target_resource:
        return list(scopes)

    remapped: list[str] = []
    for scope in scopes:
        if scope.startswith(origin_resource):
            remapped.append(f"{target_resource}{scope[len(origin_resource) :]}")
        else:
            remapped.append(scope)
    return remapped


def map_management_scopes(scopes: list[str], cloud: AzureCloud) -> list[str]:
    """Map Azure Resource Manager scopes to the target cloud."""
    return _remap_scopes_for_cloud(scopes, cloud, resource_attr="management_resource")


def map_log_analytics_scopes(scopes: list[str], cloud: AzureCloud) -> list[str]:
    """Map Azure Log Analytics scopes to the target cloud."""
    return _remap_scopes_for_cloud(
        scopes, cloud, resource_attr="log_analytics_resource"
    )


def map_graph_scopes(scopes: list[str], cloud: AzureCloud) -> list[str]:
    """Map Microsoft Graph scopes to the target cloud."""
    return _remap_scopes_for_cloud(scopes, cloud, resource_attr="graph_resource")


def get_management_scopes(cloud: AzureCloud, *, delegated: bool) -> list[str]:
    """Return default Azure Resource Manager scopes for the cloud."""
    public_resource = AZURE_CLOUD_CONFIG[AzureCloud.PUBLIC].management_resource
    base_scopes = (
        ["offline_access", f"{public_resource}/user_impersonation"]
        if delegated
        else [f"{public_resource}/.default"]
    )
    return map_management_scopes(base_scopes, cloud)


def get_log_analytics_scopes(cloud: AzureCloud, *, delegated: bool) -> list[str]:
    """Return default Azure Log Analytics scopes for the cloud."""
    public_resource = AZURE_CLOUD_CONFIG[AzureCloud.PUBLIC].log_analytics_resource
    base_scopes = (
        ["offline_access", f"{public_resource}/user_impersonation"]
        if delegated
        else [f"{public_resource}/.default"]
    )
    return map_log_analytics_scopes(base_scopes, cloud)


def get_graph_scopes(cloud: AzureCloud, *, delegated: bool) -> list[str]:
    """Return default Microsoft Graph scopes for the cloud."""
    public_resource = AZURE_CLOUD_CONFIG[AzureCloud.PUBLIC].graph_resource
    base_scopes = (
        ["offline_access", f"{public_resource}/User.Read"]
        if delegated
        else [f"{public_resource}/.default"]
    )
    return map_graph_scopes(base_scopes, cloud)
