import {
  buildSlackManifest,
  buildSlackPlatformManifest,
} from "@/components/agents/external-channels/slack-manifest"

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
      token_rotation_enabled: true,
      app_level_token_rotation_enabled: true,
      event_subscriptions: { bot_events: ["app_mention", "message.im"] },
    })
    expect(manifest.oauth_config).toEqual(
      expect.objectContaining({ redirect_urls: [REDIRECT] })
    )
  })

  it("rotates the bot token of an HTTP app, which has no app-level token", () => {
    const endpointUrl = "https://api.example.com/agent/channel-events/slack/abc"
    const manifest = buildSlackManifest({
      endpointUrl,
      oauthRedirectUrl: REDIRECT,
      appName: "Triage",
    })
    expect(manifest.settings).toEqual({
      interactivity: { is_enabled: true, request_url: endpointUrl },
      org_deploy_enabled: false,
      socket_mode_enabled: false,
      token_rotation_enabled: true,
      event_subscriptions: {
        request_url: endpointUrl,
        bot_events: ["app_mention", "message.im"],
      },
    })
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
      legacy: true,
    })
    expect(JSON.stringify(manifest, null, 2)).toBe(
      JSON.stringify(legacy, null, 2)
    )
  })

  it("labels a channel bot an agent and lets users message it", () => {
    const manifest = buildSlackManifest({
      endpointUrl: null,
      oauthRedirectUrl: REDIRECT,
      appName: "Triage",
    }) as {
      features: Record<string, unknown>
      oauth_config: { scopes: { bot: string[] } }
    }
    expect(manifest.features).toEqual(
      expect.objectContaining({
        app_home: expect.objectContaining({
          messages_tab_read_only_enabled: false,
        }),
        agent_view: { agent_description: "Tracecat agent" },
      })
    )
    expect(manifest.oauth_config.scopes.bot).toContain("assistant:write")
  })

  it("lets a custom app list the channels it is in, never join one", () => {
    const manifest = buildSlackManifest({
      endpointUrl: null,
      oauthRedirectUrl: REDIRECT,
      appName: "Triage",
    }) as { oauth_config: { scopes: { bot: string[] } } }
    expect(manifest.oauth_config.scopes.bot).toEqual(
      expect.arrayContaining(["channels:read", "groups:read"])
    )
    expect(manifest.oauth_config.scopes.bot).not.toContain("channels:join")
  })
})

describe("buildSlackPlatformManifest", () => {
  type Manifest = {
    display_information: { name: string }
    features: { bot_user: { display_name: string } }
    oauth_config: { scopes: { bot: string[] } }
    settings: Record<string, unknown>
  }

  it("names the app Tracecat and asks for channel access up front", () => {
    const manifest = buildSlackPlatformManifest({
      endpointUrl: "https://api.example.com/platform-events",
      oauthRedirectUrl: REDIRECT,
    }) as Manifest
    expect(manifest.display_information.name).toBe("Tracecat")
    expect(manifest.features.bot_user.display_name).toBe("Tracecat")
    expect(manifest.oauth_config.scopes.bot).toEqual([
      "app_mentions:read",
      "assistant:write",
      "channels:history",
      "channels:join",
      "channels:read",
      "chat:write",
      "chat:write.customize",
      "groups:history",
      "groups:read",
      "im:history",
      "mpim:history",
      "reactions:read",
      "reactions:write",
      "users:read",
      "users:read.email",
    ])
    expect(manifest.settings).toEqual(
      expect.objectContaining({ token_rotation_enabled: true })
    )
    expect(manifest.settings).not.toHaveProperty(
      "app_level_token_rotation_enabled"
    )
  })

  it("rotates the app-level token over Socket Mode", () => {
    const manifest = buildSlackPlatformManifest({
      endpointUrl: null,
      oauthRedirectUrl: REDIRECT,
    }) as Manifest
    expect(manifest.settings).toEqual(
      expect.objectContaining({
        socket_mode_enabled: true,
        token_rotation_enabled: true,
        app_level_token_rotation_enabled: true,
      })
    )
  })
})
