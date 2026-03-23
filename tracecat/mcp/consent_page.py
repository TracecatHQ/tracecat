"""Rendered consent page for MCP OAuth flows."""

from __future__ import annotations

import html

_LOGO_SVG_PATH = (
    "M261.456 68.1865C253.628 78.8783 245.659 90.7908 240.215 99.3206L234.056"
    " 108.973L222.846 110.522C123.266 124.283 49.3346 204.676 49.3346 298.91C"
    " 49.3346 402.366 138.692 489.349 252.84 489.349C366.987 489.349 456.345"
    " 402.366 456.345 298.91C456.345 272.743 450.717 247.836 440.51 225.141L"
    "485.372 204.259C498.435 233.304 505.68 265.317 505.68 298.91C505.68"
    " 433.526 390.725 539.539 252.84 539.539C114.955 539.539 0 433.526 0"
    " 298.91C0 180.275 89.4713 83.6982 204.954 62.5939C211.414 52.8463"
    " 219.42 41.2854 227.08 31.2619C232.164 24.6104 237.631 17.9264 242.706"
    " 12.8398C245.15 10.3898 248.357 7.43692 252.022 5.07425C253.86 3.88898"
    " 256.633 2.31261 260.123 1.23909C263.537 0.189061 269.401 -0.910787"
    " 276.139 1.21079C284.943 3.98294 289.95 10.3077 292.063 13.3053C294.532"
    " 16.8064 296.304 20.5241 297.527 23.3536C299.427 27.7515 301.309 33.2062"
    " 302.832 37.6211C303.208 38.711 303.563 39.7375 303.89 40.6692C305.279"
    " 44.6261 306.424 47.6275 307.418 49.8493C326.525 54.1155 357.134 61.9477"
    " 377.952 67.2747C379.459 67.6605 380.916 68.0331 382.313 68.3903C388.73"
    " 64.0835 396.285 59.4715 403.848 55.712C409.735 52.785 416.722 49.8186"
    " 423.791 48.2435C429.641 46.94 441.939 45.0794 453.115 52.5971L462.517"
    " 58.9219L463.971 70.2935C471.374 128.204 454.415 194.788 418.555"
    " 238.317C400.323 260.447 376.215 277.729 346.885 283.278C317.261"
    " 288.882 285.571 281.897 253.683 261.533L279.913 219.025C303.413"
    " 234.032 322.656 236.811 337.866 233.934C353.368 231.001 367.992"
    " 221.557 380.744 206.078C401.373 181.037 414.449 143.211 416.16"
    " 106.009C410.774 109.286 405.66 112.825 401.922 115.65L392.58"
    " 122.71L381.284 119.864C376.943 118.771 371.274 117.321 364.838"
    " 115.675C341.296 109.653 307.494 101.007 290.939 97.5985C276.198"
    " 94.5637 268.666 82.3324 265.783 77.1863C264.166 74.2989 262.727"
    " 71.2126 261.456 68.1865ZM434.729 97.1981C434.729 97.1984 434.715"
    " 97.2006 434.687 97.2038C434.715 97.1994 434.729 97.1978 434.729"
    " 97.1981ZM309.4 53.4976C309.396 53.5217 309.257 53.3574 308.995"
    " 52.9324C309.272 53.261 309.404 53.4735 309.4 53.4976Z"
)


def _get_tracecat_logo_markup(fill_color: str = "#1C1C1C") -> str:
    """Return inline SVG markup for the Tracecat logo mark."""
    return (
        '<svg aria-label="Tracecat" width="30" height="30" viewBox="0 0 506 540"'
        f' fill="none" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill-rule="evenodd" clip-rule="evenodd" d="{_LOGO_SVG_PATH}"'
        f' fill="{fill_color}"/></svg>'
    )


def build_oidc_consent_html(
    *,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    txn_id: str,
    csrf_token: str,
) -> str:
    """Render a custom consent page for the OIDC interactive flow."""
    escaped_client_id = html.escape(client_id, quote=True)
    escaped_redirect_uri = html.escape(redirect_uri, quote=True)
    escaped_txn_id = html.escape(txn_id, quote=True)
    escaped_csrf_token = html.escape(csrf_token, quote=True)
    scope_items = (
        "".join(f"<li>{html.escape(scope, quote=True)}</li>" for scope in scopes)
        or "<li>No scopes requested</li>"
    )
    logo_markup = _get_tracecat_logo_markup(fill_color="#FFFFFF")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Authorize MCP client</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: #ffffff;
      color: #111827;
    }}
    .stack {{
      width: min(520px, 100%);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 18px;
    }}
    .logo-badge {{
      width: 64px;
      height: 64px;
      border-radius: 14px;
      background: #111827;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #111827;
    }}
    .card {{
      width: 100%;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
      background: #ffffff;
      padding: 20px;
    }}
    h1 {{
      margin: 0;
      font-size: 1.625rem;
      line-height: 1.2;
      letter-spacing: -0.01em;
      font-weight: 600;
    }}
    .subtitle {{
      margin: 8px 0 0;
      color: #6b7280;
      font-size: 0.95rem;
      line-height: 1.5;
    }}
    .panel {{
      margin-top: 16px;
      padding: 12px;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      background: #f9fafb;
      font-size: 0.92rem;
      line-height: 1.5;
    }}
    .kv-label {{
      color: #6b7280;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-top: 8px;
    }}
    .kv-label:first-child {{
      margin-top: 0;
    }}
    code {{
      display: block;
      margin-top: 4px;
      padding: 6px 8px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #ffffff;
      color: #111827;
      font-size: 0.8rem;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }}
    .scopes-title {{
      margin-top: 10px;
      color: #6b7280;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    ul {{
      margin: 8px 0 0;
      padding-left: 18px;
    }}
    .actions {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
    }}
    .decision {{
      appearance: none;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      padding: 9px 14px;
      background: #ffffff;
      color: #111827;
      font-weight: 600;
      font-size: 0.9rem;
      cursor: pointer;
      min-width: 96px;
    }}
    .decision.primary {{
      background: #111827;
      border-color: #111827;
      color: #ffffff;
    }}
    .footnote {{
      margin-top: 10px;
      color: #6b7280;
      font-size: 0.78rem;
    }}
    .footnote code {{
      display: inline;
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
      font-size: inherit;
    }}
  </style>
</head>
<body>
  <div class="stack">
    <div class="logo-badge">
      {logo_markup}
    </div>
    <div class="card">
      <h1>Authorize MCP client</h1>
      <p class="subtitle">This client is requesting access to your Tracecat account.</p>
      <div class="panel">
        <div class="kv-label">Client ID</div>
        <code>{escaped_client_id}</code>
        <div class="kv-label">Redirect URI</div>
        <code>{escaped_redirect_uri}</code>
        <div class="scopes-title">Requested scopes</div>
        <ul>{scope_items}</ul>
        <div class="footnote">Transaction: <code>{escaped_txn_id}</code></div>
      </div>
      <form action="/consent" method="post">
        <input type="hidden" name="txn_id" value="{escaped_txn_id}" />
        <input type="hidden" name="csrf_token" value="{escaped_csrf_token}" />
        <div class="actions">
          <button class="decision primary" type="submit" name="action" value="approve">Allow</button>
          <button class="decision" type="submit" name="action" value="deny">Deny</button>
        </div>
      </form>
    </div>
  </div>
</body>
</html>"""
