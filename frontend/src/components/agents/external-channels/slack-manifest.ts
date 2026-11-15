/** The Slack app manifest for a bot; a null endpoint means Socket Mode. */
export function buildSlackManifest({
  endpointUrl,
  oauthRedirectUrl,
  appName,
  legacy = false,
}: {
  /** Where Slack posts events and interactions, or null over Socket Mode. */
  endpointUrl: string | null
  oauthRedirectUrl: string
  appName: string
  /** The legacy channel token path: mentions only, and no agent_view (one-way per app). */
  legacy?: boolean
}): Record<string, unknown> {
  const socket = endpointUrl === null
  const requestUrl = socket ? {} : { request_url: endpointUrl }
  const settings = {
    interactivity: { is_enabled: true, ...requestUrl },
    org_deploy_enabled: false,
    socket_mode_enabled: socket,
    // Slack cannot turn rotation off again; the legacy path cannot refresh tokens.
    token_rotation_enabled: !legacy,
    ...(socket ? { app_level_token_rotation_enabled: true } : {}),
    event_subscriptions: {
      ...requestUrl,
      bot_events: legacy ? ["app_mention"] : ["app_mention", "message.im"],
    },
  }

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
        messages_tab_read_only_enabled: legacy,
      },
      bot_user: {
        display_name: appName,
        always_online: true,
      },
      // Slack labels the app an agent. One-way: an app on agent_view cannot go back.
      ...(legacy
        ? {}
        : { agent_view: { agent_description: "Tracecat agent" } }),
    },
    oauth_config: {
      scopes: {
        bot: [
          "app_mentions:read",
          // Agent label, and `assistant.threads.setStatus`.
          ...(legacy ? [] : ["assistant:write"]),
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
        ],
      },
      redirect_urls: [oauthRedirectUrl],
    },
    settings,
  }
}
