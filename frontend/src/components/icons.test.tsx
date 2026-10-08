import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import {
  DatabricksIcon,
  getIcon,
  getIconSource,
  getModelProviderIconId,
  MicrosoftGraphIcon,
  MicrosoftIcon,
  MicrosoftOutlookIcon,
  OnePasswordIcon,
  providerIcons,
  SnowflakeIcon,
  secretIcons,
  UDFIcons,
} from "@/components/icons"

const DATABRICKS_RED = "#FF3621"
const SNOWFLAKE_BLUE = "#29B5E8"

/** Fragment ids declared by the official Microsoft marks. */
const GRAPH_CLIP_ID = "tracecat-microsoft-graph-clip"
const OUTLOOK_GRADIENT_ID = "tracecat-microsoft-outlook-gradient"

/** Ids of every `<clipPath>`/gradient the rendered markup declares. */
function renderedFragmentIds(node: ReactElement): string[] {
  const { container } = render(node)
  return Array.from(
    container.querySelectorAll("clipPath, linearGradient, radialGradient")
  ).map((element) => element.id)
}

/** Every id referenced through a `url(#…)` attribute value. */
function referencedFragmentIds(container: HTMLElement): string[] {
  const references: string[] = []
  for (const element of Array.from(container.querySelectorAll("*"))) {
    for (const attribute of Array.from(element.attributes)) {
      const match = attribute.value.match(/^url\(#(.+)\)$/)
      if (match?.[1]) {
        references.push(match[1])
      }
    }
  }
  return references
}

/** Render an icon element and return the rendered container. */
function renderIcon(element: JSX.Element): HTMLElement {
  return render(element).container
}

/** Read the accessible title of the first SVG an icon element renders. */
function svgTitle(element: JSX.Element): string | null {
  return renderIcon(element).querySelector("svg > title")?.textContent ?? null
}

describe("Microsoft registry icon mappings", () => {
  it.each(["tools.microsoft_graph_sdk", "tools.microsoft_graph_security"])(
    "renders the Microsoft Graph mark for %s",
    (namespace) => {
      const Icon = UDFIcons[namespace]
      expect(renderedFragmentIds(<Icon />)).toContain(GRAPH_CLIP_ID)
    }
  )

  it("renders the Microsoft Outlook mark for its tool namespace", () => {
    const Icon = UDFIcons["tools.microsoft_outlook"]
    expect(renderedFragmentIds(<Icon />)).toContain(OUTLOOK_GRADIENT_ID)
  })

  it.each(["microsoft_graph", "microsoft_graph_security"])(
    "renders the Microsoft Graph mark for the %s provider",
    (providerId) => {
      const Icon = providerIcons[providerId]
      expect(renderedFragmentIds(<Icon />)).toContain(GRAPH_CLIP_ID)
    }
  )

  it("renders the Microsoft Outlook mark for the microsoft_outlook provider", () => {
    const Icon = providerIcons.microsoft_outlook
    expect(renderedFragmentIds(<Icon />)).toContain(OUTLOOK_GRADIENT_ID)
  })
})

describe("Microsoft icon SVG fragment ids", () => {
  it("keeps the Graph and Outlook marks from sharing fragment ids", () => {
    const graphIds = new Set(renderedFragmentIds(<MicrosoftGraphIcon />))
    const outlookIds = renderedFragmentIds(<MicrosoftOutlookIcon />)
    expect(outlookIds.filter((id) => graphIds.has(id))).toEqual([])
  })

  it("resolves every url(#…) reference when both marks share a page", () => {
    const { container } = render(
      <>
        <MicrosoftGraphIcon />
        <MicrosoftOutlookIcon />
        <MicrosoftGraphIcon />
      </>
    )
    const declared = new Set(
      Array.from(
        container.querySelectorAll("clipPath, linearGradient, radialGradient")
      ).map((element) => element.id)
    )
    const referenced = referencedFragmentIds(container)
    expect(referenced.length).toBeGreaterThan(0)
    expect(referenced.filter((id) => !declared.has(id))).toEqual([])
  })
})

describe("MicrosoftIcon sizing", () => {
  it("applies className and leaves sizing to CSS", () => {
    const { container } = render(<MicrosoftIcon className="size-full" />)
    const svg = container.querySelector("svg")
    expect(svg).toHaveClass("size-full")
    expect(svg).not.toHaveAttribute("width")
    expect(svg).not.toHaveAttribute("height")
  })

  it("is sized by the shared registry renderer", () => {
    const Icon = UDFIcons["tools.microsoft_graph_security"]
    const { container } = render(<Icon />)
    expect(container.querySelector("svg")).toHaveClass("size-full")
  })
})

describe("Databricks and Snowflake brand marks", () => {
  it("renders self-contained vectors in the official brand colors", () => {
    const databricks = renderIcon(<DatabricksIcon />)
    expect(databricks.querySelector("svg > title")?.textContent).toBe(
      "Databricks"
    )
    expect(
      databricks.querySelector(`[fill="${DATABRICKS_RED}"]`)
    ).not.toBeNull()

    const snowflake = renderIcon(<SnowflakeIcon />)
    expect(snowflake.querySelector("svg > title")?.textContent).toBe(
      "Snowflake"
    )
    expect(
      snowflake.querySelectorAll(`[fill="${SNOWFLAKE_BLUE}"]`).length
    ).toBe(2)

    // No <image>: the marks must stay vector, never an embedded raster.
    expect(databricks.querySelector("image")).toBeNull()
    expect(snowflake.querySelector("image")).toBeNull()
  })

  it("keeps the registration mark that Snowflake's brand guide requires", () => {
    // The bug occupies the first 52.15 units of the viewBox; the second path is
    // the registration mark tucked into its empty top-right corner.
    const snowflake = renderIcon(<SnowflakeIcon />)
    const svg = snowflake.querySelector("svg")
    expect(svg?.getAttribute("viewBox")).toBe("0 0 54.26 51.02")
    expect(svg?.querySelectorAll("path")).toHaveLength(2)
  })
})

describe("1Password brand mark", () => {
  it("renders the official vector mark in its primary blue", () => {
    const onepassword = renderIcon(<OnePasswordIcon />)

    expect(onepassword.querySelector("svg > title")?.textContent).toBe(
      "1Password"
    )
    expect(onepassword.querySelector('[stop-color="#1D48F5"]')).not.toBeNull()
    expect(onepassword.querySelector("image")).toBeNull()
  })
})

describe("action namespace icons", () => {
  const cases: [namespace: string, title: string][] = [
    ["tools.databricks", "Databricks"],
    ["tools.databricks.jobs.run_job", "Databricks"],
    ["tools.databricks_sdk", "Databricks"],
    ["tools.databricks_sdk.call_method", "Databricks"],
    ["tools.1password", "1Password"],
    ["tools.1password.list_audit_events", "1Password"],
    ["tools.1password_sdk", "1Password"],
    ["tools.1password_sdk.call_method", "1Password"],
    ["tools.snowflake", "Snowflake"],
    ["tools.snowflake.execute_statement", "Snowflake"],
  ]

  it.each(cases)("maps %s to the %s mark", (namespace, title) => {
    expect(svgTitle(getIcon(namespace))).toBe(title)
  })

  it("falls back to a generic glyph for unregistered namespaces", () => {
    // Proves the assertions above are not satisfied by the generic fallback.
    expect(svgTitle(getIcon("tools.unregistered_vendor"))).toBeNull()
  })
})

describe("Fortinet registry icon mappings", () => {
  it.each(["tools.fortisiem", "tools.fortisiem.run_event_query"])(
    "renders the Fortinet mark for %s",
    (key) => {
      expect(svgTitle(getIcon(key))).toBe("Fortinet")
    }
  )

  it("renders the Fortinet mark for the fortisiem secret", () => {
    const Icon = secretIcons.fortisiem
    expect(svgTitle(<Icon />)).toBe("Fortinet")
  })
})

describe("Palo Alto Networks registry icon mappings", () => {
  it.each([
    "tools.pan_os",
    "tools.pan_strata",
    "tools.pan_prisma",
    "tools.pan_os.block_ip",
    "tools.pan_prisma.dismiss_alerts",
    "tools.pan_strata.push_candidate_config",
  ])("renders the Palo Alto Networks mark for %s", (key) => {
    expect(svgTitle(getIcon(key))).toBe("Palo Alto Networks")
  })

  it("renders the Palo Alto Networks mark for the pan_strata provider", () => {
    const Icon = providerIcons.pan_strata
    expect(svgTitle(<Icon />)).toBe("Palo Alto Networks")
  })

  it.each(["pan_os", "pan_prisma"] as const)(
    "renders the Palo Alto Networks mark for the %s secret",
    (name) => {
      const Icon = secretIcons[name]
      expect(svgTitle(<Icon />)).toBe("Palo Alto Networks")
    }
  )
})

describe("OAuth provider and credential icons", () => {
  const providerCases: [providerId: string, title: string][] = [
    ["databricks", "Databricks"],
    ["snowflake", "Snowflake"],
  ]
  const secretCases: [secretName: string, title: string][] = [
    ...providerCases,
    ["onepassword", "1Password"],
    ["onepassword_events", "1Password"],
  ]

  it.each(providerCases)(
    "maps the %s provider to the %s mark",
    (providerId, title) => {
      const Icon = providerIcons[providerId]
      expect(Icon).toBeDefined()
      expect(svgTitle(<Icon />)).toBe(title)
    }
  )

  it.each(secretCases)(
    "maps the %s secret to the %s mark",
    (secretName, title) => {
      const Icon = secretIcons[secretName]
      expect(Icon).toBeDefined()
      expect(svgTitle(<Icon />)).toBe(title)
    }
  )
})

describe("icon source identities", () => {
  it.each([
    "tools.slack_sdk",
    "tools.slack_blocks",
    "tools.slack.post_message",
  ])("shares the Slack icon with %s", (key) => {
    expect(getIconSource(key)).toBe(getIconSource("tools.slack"))
  })

  it("distinguishes unrelated icons", () => {
    expect(getIconSource("tools.slack")).not.toBe(getIconSource("tools.jira"))
  })

  it("shares catalog icons and resolves their action keys", () => {
    expect(getIconSource("tools.crowdstrike")).toBe(
      getIconSource("tools.falconpy.call_method")
    )
    expect(getIconSource("tools.crowdstrike")).not.toBe(
      getIconSource("tools.splunk")
    )
  })

  it("resolves Lucide icons and generic fallbacks consistently", () => {
    expect(getIconSource("tools.abuseipdb.check_ip")).toBe(
      getIconSource("tools.abuseipdb")
    )
    expect(getIconSource("tools.abuseipdb")).not.toBe(
      getIconSource("tools.anyrun")
    )
    expect(getIconSource("unknown.action")).toBe(getIconSource("unknown"))
    expect(getIconSource("tools.unknown")).toBe(getIconSource("tools"))
  })
})

describe("model provider icons", () => {
  it.each([
    ["anthropic", "anthropic"],
    ["azure_ai", "microsoft"],
    ["azure_openai", "microsoft"],
    ["bedrock", "amazon-bedrock"],
    ["gemini", "google"],
    ["vertex_ai", "google"],
    ["mistral", "mistral"],
    ["openai", "openai"],
    ["unknown", "custom"],
    ["", "custom"],
  ])("maps %s to %s", (provider, iconId) => {
    expect(getModelProviderIconId(provider)).toBe(iconId)
  })
})
