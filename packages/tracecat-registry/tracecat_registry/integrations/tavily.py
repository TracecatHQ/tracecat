from tavily import AsyncTavilyClient
from typing import Any, Annotated, Literal
from typing_extensions import Doc
from tracecat_registry import RegistrySecret, registry, secrets

tavily_secret = RegistrySecret(name="tavily", keys=["TAVILY_API_KEY"])
"""Tavily API key.

- name: `tavily`
- keys:
    - `TAVILY_API_KEY`
"""


@registry.register(
    default_title="Search the web",
    description="Search the web with Tavily for a given search query.",
    display_group="Tavily",
    doc_url="https://docs.tavily.com/api-reference/endpoint/search",
    namespace="tools.tavily",
    secrets=[tavily_secret],
)
async def web_search(
    query: Annotated[str, Doc("Search query to execute with Tavily.")],
    search_deep: Annotated[Literal["basic", "advanced"], Doc("Depth of the search.")],
    topic: Annotated[Literal["general", "news"], Doc("Category of the search.")],
    time_range: Annotated[
        Literal["day", "week", "month", "year"],
        Doc("Time range back from the current date to filter results."),
    ],
) -> dict[str, Any]:
    client = AsyncTavilyClient(api_key=secrets.get("TAVILY_API_KEY"))
    result = await client.search(
        query,
        search_deep=search_deep,
        topic=topic,
        time_range=time_range,
    )
    return result
