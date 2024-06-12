from datetime import datetime

from uim.integrations.common import list_microsoft_graph_alerts

MICROSOFT_GRAPH_SERVICE_SOURCE = "microsoftDefenderForEndpoint"


async def list_defender_endpoint_alerts(
    client_id: str,
    client_secret: str,
    tenant_id: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
):
    return await list_microsoft_graph_alerts(
        client_id=client_id,
        client_secret=client_secret,
        tenant_id=tenant_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        service_source=MICROSOFT_GRAPH_SERVICE_SOURCE,
    )
