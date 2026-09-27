"""Browserless browser automation actions.

Browserless exposes a managed Chrome over REST. These actions are Python rather
than YAML templates because every one of them takes a caller-supplied target URL
that a browser then opens, and that target has to pass the registry's egress
policy before the request is handed over; a template cannot enforce that.

The Browserless `base_url` itself is ordinary routing configuration and is used
as configured, exactly like any other self-hosted integration endpoint. Only the
page being opened is checked.
"""
# WARNING: Do not import __future__ annotations from typing
# Causes class types to resolve as strings, breaking TypedDict runtime behavior

from typing import Annotated, Any

import httpx
from typing_extensions import Doc

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry._internal.network import validate_public_target_url
from tracecat_registry.core.http import HTTPResponse, httpx_to_response

browserless_secret = RegistrySecret(name="browserless", keys=["BROWSERLESS_TOKEN"])
"""Browserless API token.

- name: `browserless`
- keys:
    - `BROWSERLESS_TOKEN`

Note: the token is sent as a `Bearer` credential. Browserless also documents a
`token` query parameter, which would place the credential in access logs and
referrer headers, so it is not used here.
"""

BROWSERLESS_BASE_URL = "https://production-sfo.browserless.io"

BaseUrl = Annotated[
    str | None,
    Doc(
        "Browserless base URL. Defaults to `https://production-sfo.browserless.io`. "
        "Set it to your own instance when self-hosting."
    ),
]
TargetUrl = Annotated[
    str,
    Doc(
        "The URL to navigate to. Must be an http or https URL that resolves to a "
        "publicly routable address."
    ),
]
GotoOptions = Annotated[
    dict[str, Any] | None,
    Doc(
        # Generic: Puppeteer's own `page.goto` options object, which Browserless
        # forwards verbatim and documents as free-form.
        "Options forwarded to Puppeteer's `page.goto`, for example "
        '`{"waitUntil": "networkidle2", "timeout": 30000}`.'
    ),
]
WaitForSelector = Annotated[
    dict[str, Any] | None,
    Doc(
        # Generic: Browserless documents this as Puppeteer's `waitForSelector`
        # argument object rather than a fixed field set.
        "Wait for a CSS selector before capturing, for example "
        '`{"selector": "#results", "timeout": 10000}`.'
    ),
]
BestAttempt = Annotated[
    bool | None,
    Doc(
        "Continue and return whatever rendered so far when a `waitFor` or "
        "navigation step times out."
    ),
]
Timeout = Annotated[float, Doc("Timeout in seconds for the Browserless request.")]


async def _post(
    *,
    endpoint: str,
    base_url: str | None,
    payload: dict[str, Any],
    timeout: float,
    base64_encode_data: bool = False,
) -> HTTPResponse:
    """Send one request to Browserless and return the untouched HTTP envelope."""
    url = f"{(base_url or BROWSERLESS_BASE_URL).rstrip('/')}{endpoint}"
    # Browserless validates each body against a JSON schema that rejects an
    # explicit null on an optional field, so unset options are omitted.
    body = {key: value for key, value in payload.items() if value is not None}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {secrets.get('BROWSERLESS_TOKEN')}"},
            json=body,
        )
    response.raise_for_status()
    return httpx_to_response(response, base64_encode_data=base64_encode_data)


@registry.register(
    default_title="Get page content",
    description="Render a URL in a managed Chrome browser and return its HTML.",
    display_group="Browserless",
    doc_url="https://docs.browserless.io/rest-apis/content",
    namespace="tools.browserless",
    secrets=[browserless_secret],
)
async def get_content(
    url: TargetUrl,
    base_url: BaseUrl = None,
    goto_options: GotoOptions = None,
    wait_for_selector: WaitForSelector = None,
    best_attempt: BestAttempt = None,
    timeout: Timeout = 30.0,
) -> HTTPResponse:
    await validate_public_target_url(url)
    return await _post(
        endpoint="/content",
        base_url=base_url,
        payload={
            "url": url,
            "gotoOptions": goto_options,
            "waitForSelector": wait_for_selector,
            "bestAttempt": best_attempt,
        },
        timeout=timeout,
    )


@registry.register(
    default_title="Take screenshot",
    description="Render a URL in a managed Chrome browser and return a base64-encoded screenshot in `data`, with its media type in `headers`.",
    display_group="Browserless",
    doc_url="https://docs.browserless.io/rest-apis/screenshot",
    namespace="tools.browserless",
    secrets=[browserless_secret],
)
async def take_screenshot(
    url: TargetUrl,
    base_url: BaseUrl = None,
    options: Annotated[
        dict[str, Any] | None,
        Doc(
            # Generic: Puppeteer's own `page.screenshot` options object, which
            # Browserless forwards verbatim and documents as free-form.
            "Options forwarded to Puppeteer's `page.screenshot`. Documented "
            "fields include fullPage, type (png, jpeg or webp), quality, "
            "omitBackground, clip and encoding."
        ),
    ] = None,
    selector: Annotated[
        str | None, Doc("Capture only the element matching this CSS selector.")
    ] = None,
    scroll_page: Annotated[
        bool | None, Doc("Scroll the page before capturing so lazy content loads.")
    ] = None,
    goto_options: GotoOptions = None,
    wait_for_selector: WaitForSelector = None,
    best_attempt: BestAttempt = None,
    timeout: Timeout = 30.0,
) -> HTTPResponse:
    await validate_public_target_url(url)
    return await _post(
        endpoint="/screenshot",
        base_url=base_url,
        payload={
            "url": url,
            "options": options,
            "selector": selector,
            "scrollPage": scroll_page,
            "gotoOptions": goto_options,
            "waitForSelector": wait_for_selector,
            "bestAttempt": best_attempt,
        },
        timeout=timeout,
        base64_encode_data=True,
    )


@registry.register(
    default_title="Scrape elements",
    description="Render a URL in a managed Chrome browser and extract elements by CSS selector.",
    display_group="Browserless",
    doc_url="https://docs.browserless.io/rest-apis/scrape",
    namespace="tools.browserless",
    secrets=[browserless_secret],
)
async def scrape_elements(
    url: TargetUrl,
    elements: Annotated[
        list[dict[str, Any]],
        Doc(
            # Generic: Browserless documents each entry as a free-form selector
            # object rather than a fixed field set.
            'Elements to extract, for example `[{"selector": "h1"}]`. '
            "Documented fields per entry are selector and timeout."
        ),
    ],
    base_url: BaseUrl = None,
    goto_options: GotoOptions = None,
    wait_for_selector: WaitForSelector = None,
    best_attempt: BestAttempt = None,
    timeout: Timeout = 30.0,
) -> HTTPResponse:
    await validate_public_target_url(url)
    return await _post(
        endpoint="/scrape",
        base_url=base_url,
        payload={
            "url": url,
            "elements": elements,
            "gotoOptions": goto_options,
            "waitForSelector": wait_for_selector,
            "bestAttempt": best_attempt,
        },
        timeout=timeout,
    )
