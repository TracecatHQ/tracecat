import { render } from "@testing-library/react"
import type { ReactElement } from "react"
import {
  MicrosoftGraphIcon,
  MicrosoftIcon,
  MicrosoftOutlookIcon,
  providerIcons,
  UDFIcons,
} from "@/components/icons"

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

describe("Microsoft registry icon mappings", () => {
  it.each([
    "tools.microsoft_graph_sdk",
    "tools.microsoft_graph_security",
    "tools.microsoft_graph_security_sdk",
  ])("renders the Microsoft Graph mark for %s", (namespace) => {
    const Icon = UDFIcons[namespace]
    expect(renderedFragmentIds(<Icon />)).toContain(GRAPH_CLIP_ID)
  })

  it.each(["tools.microsoft_outlook", "tools.microsoft_outlook_sdk"])(
    "renders the Microsoft Outlook mark for %s",
    (namespace) => {
      const Icon = UDFIcons[namespace]
      expect(renderedFragmentIds(<Icon />)).toContain(OUTLOOK_GRADIENT_ID)
    }
  )

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
