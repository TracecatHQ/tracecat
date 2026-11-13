/** The Slack app manifest for a bot; a null endpoint means Socket Mode. */
export function buildSlackManifest({
  endpointUrl,
  oauthRedirectUrl,
  appName,
}: {
  /** Where Slack posts events and interactions, or null over Socket Mode. */
  endpointUrl: string | null
  oauthRedirectUrl: string
  appName: string
}): Record<string, unknown> {
  const requestUrl = endpointUrl === null ? {} : { request_url: endpointUrl }
  const settings = {
    interactivity: { is_enabled: true, ...requestUrl },
    org_deploy_enabled: false,
    socket_mode_enabled: endpointUrl === null,
    token_rotation_enabled: false,
    event_subscriptions: { ...requestUrl, bot_events: ["app_mention"] },
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
        messages_tab_read_only_enabled: true,
      },
      bot_user: {
        display_name: appName,
        always_online: true,
      },
    },
    oauth_config: {
      scopes: {
        bot: [
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
        ],
      },
      redirect_urls: [oauthRedirectUrl],
    },
    settings,
  }
}
