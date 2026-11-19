// The legacy channel token path's scopes; matches SLACK_OAUTH_SCOPES in tracecat/agent/channels/service.py. Frozen.
const LEGACY_BOT_SCOPES = [
  "app_mentions:read",
  "channels:history",
  "chat:write",
  "chat:write.customize",
  "groups:history",
  "im:history",
  "mpim:history",
  "reactions:read",
  "reactions:write",
  "users:read",
  "users:read.email",
] as const

/** The legacy channel token path's Slack app manifest: mentions only, no agent_view. Frozen. */
export function buildLegacySlackManifest({
  endpointUrl,
  oauthRedirectUrl,
  appName,
}: {
  /** Where Slack posts events and interactions. */
  endpointUrl: string
  oauthRedirectUrl: string
  appName: string
}): Record<string, unknown> {
  return {
    display_information: {
      name: appName,
      description: "Tracecat external channel agent",
      background_color: "#111827",
    },
    features: {
      app_home: {
        home_tab_enabled: false,
        messages_tab_enabled: true,
        messages_tab_read_only_enabled: true,
      },
      bot_user: {
        display_name: appName,
        always_online: true,
      },
    },
    oauth_config: {
      scopes: { bot: LEGACY_BOT_SCOPES },
      redirect_urls: [oauthRedirectUrl],
    },
    settings: {
      interactivity: { is_enabled: true, request_url: endpointUrl },
      org_deploy_enabled: false,
      socket_mode_enabled: false,
      // Slack cannot turn rotation off again, and the legacy path cannot refresh tokens.
      token_rotation_enabled: false,
      event_subscriptions: {
        request_url: endpointUrl,
        bot_events: ["app_mention"],
      },
    },
  }
}

const SLACK_APPS_URL = "https://api.slack.com/apps"

/** A Slack app's settings page, or the app list before its ID is known. */
export function slackAppSettingsUrl(
  appId: string | null,
  page: "general" | "distribute" = "general"
): string {
  const id = appId?.trim()
  return id
    ? `${SLACK_APPS_URL}/${encodeURIComponent(id)}/${page}`
    : SLACK_APPS_URL
}
