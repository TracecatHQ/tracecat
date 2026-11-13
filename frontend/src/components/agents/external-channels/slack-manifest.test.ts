import { buildSlackManifest } from "@/components/agents/external-channels/slack-manifest"

const REDIRECT =
  "https://api.example.com/agent/channel-bots/slack/oauth/callback"

describe("buildSlackManifest", () => {
  it("enables Socket Mode and drops the request URLs", () => {
    const manifest = buildSlackManifest({
      endpointUrl: null,
      oauthRedirectUrl: REDIRECT,
      appName: "Triage",
    })
    expect(manifest.settings).toEqual({
      interactivity: { is_enabled: true },
      org_deploy_enabled: false,
      socket_mode_enabled: true,
      token_rotation_enabled: false,
      event_subscriptions: { bot_events: ["app_mention"] },
    })
    expect(manifest.oauth_config).toEqual(
      expect.objectContaining({ redirect_urls: [REDIRECT] })
    )
  })

  it("copies the legacy panel's manifest byte for byte", () => {
    const endpointUrl = "https://api.example.com/agent/channels/slack/abc"
    const oauthRedirectUrl =
      "https://api.example.com/agent/channels/slack/oauth/callback"
    // The legacy panel's output before the builder moved, keys in order.
    const legacy = {
      display_information: {
        name: "Triage",
        description: "Tracecat external channel agent",
        background_color: "#111827",
      },
      features: {
        app_home: {
          home_tab_enabled: false,
          messages_tab_enabled: true,
          messages_tab_read_only_enabled: true,
        },
        bot_user: { display_name: "Triage", always_online: true },
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
      settings: {
        interactivity: { is_enabled: true, request_url: endpointUrl },
        org_deploy_enabled: false,
        socket_mode_enabled: false,
        token_rotation_enabled: false,
        event_subscriptions: {
          request_url: endpointUrl,
          bot_events: ["app_mention"],
        },
      },
    }
    const manifest = buildSlackManifest({
      endpointUrl,
      oauthRedirectUrl,
      appName: "Triage",
    })
    expect(JSON.stringify(manifest, null, 2)).toBe(
      JSON.stringify(legacy, null, 2)
    )
  })
})
