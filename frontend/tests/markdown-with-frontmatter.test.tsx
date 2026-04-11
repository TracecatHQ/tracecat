import { render, screen } from "@testing-library/react"
import { MarkdownWithFrontmatter } from "@/components/ai-elements/markdown-with-frontmatter"
import { extractMarkdownFrontmatter } from "@/lib/markdown-frontmatter"

jest.mock("streamdown", () => ({
  Streamdown: ({
    children,
    className,
  }: {
    children: React.ReactNode
    className?: string
  }) => (
    <div className={className} data-testid="streamdown">
      {children}
    </div>
  ),
}))

jest.mock("yaml", () => ({
  parse: (input: string) => {
    if (input.includes("title: Incident triage")) {
      return {
        title: "Incident triage",
        description: "Handles incoming incidents.",
        tags: ["ops", "cases"],
        config: { mode: "strict" },
      }
    }

    if (input.includes("owner: Platform")) {
      return {
        owner: "Platform",
      }
    }

    throw new Error("Invalid YAML")
  },
  stringify: (value: unknown) => JSON.stringify(value, null, 2),
}))

describe("extractMarkdownFrontmatter", () => {
  it("returns parsed frontmatter and strips a duplicate leading title heading", () => {
    const parsed = extractMarkdownFrontmatter(`---
title: Incident triage
description: Handles incoming incidents.
tags:
  - ops
  - cases
config:
  mode: strict
---

# Incident triage

Runbook body.`)

    expect(parsed).not.toBeNull()
    expect(parsed?.title).toBe("Incident triage")
    expect(parsed?.description).toBe("Handles incoming incidents.")
    expect(parsed?.body).toBe("Runbook body.")
  })

  it("accepts frontmatter after leading blank lines or a BOM", () => {
    const withLeadingNewline = extractMarkdownFrontmatter(`
---
title: Incident triage
---

Body`)
    const withBom = extractMarkdownFrontmatter(`\uFEFF---
title: Incident triage
---

Body`)

    expect(withLeadingNewline?.title).toBe("Incident triage")
    expect(withLeadingNewline?.body.trim()).toBe("Body")
    expect(withBom?.title).toBe("Incident triage")
    expect(withBom?.body.trim()).toBe("Body")
  })

  it("keeps indented first lines that only look like duplicate headings", () => {
    const parsed = extractMarkdownFrontmatter(`---
title: Incident triage
---

    # Incident triage

Body`)

    expect(parsed).not.toBeNull()
    expect(parsed?.body).toBe("\n    # Incident triage\n\nBody")
  })

  it("preserves indentation on the first retained line after removing a duplicate heading", () => {
    const parsed = extractMarkdownFrontmatter(`---
title: Incident triage
---

# Incident triage

    print("hello")

Body`)

    expect(parsed).not.toBeNull()
    expect(parsed?.body).toBe('    print("hello")\n\nBody')
  })

  it("leaves markdown untouched when there is no valid frontmatter block", () => {
    expect(extractMarkdownFrontmatter("# Plain heading")).toBeNull()
    expect(
      extractMarkdownFrontmatter(`---
title: Missing terminator

# Body`)
    ).toBeNull()
  })
})

describe("MarkdownWithFrontmatter", () => {
  it("renders a metadata panel and keeps the markdown body", () => {
    render(
      <MarkdownWithFrontmatter>
        {`---
title: Incident triage
description: Handles incoming incidents.
tags:
  - ops
  - cases
config:
  mode: strict
---

# Incident triage

Runbook body.`}
      </MarkdownWithFrontmatter>
    )

    expect(
      screen.getByRole("heading", { name: "Incident triage" })
    ).toBeInTheDocument()
    expect(screen.getByText("Handles incoming incidents.")).toBeInTheDocument()
    expect(screen.getByText("ops")).toBeInTheDocument()
    expect(screen.getByText("cases")).toBeInTheDocument()
    expect(screen.getByText(/"mode": "strict"/)).toBeInTheDocument()
    expect(screen.getByTestId("streamdown")).toHaveTextContent("Runbook body.")
    expect(screen.getByText("Raw frontmatter")).toBeInTheDocument()
    expect(screen.queryByText("# Incident triage")).not.toBeInTheDocument()
  })

  it("renders frontmatter metadata when the document starts with a blank line", () => {
    render(
      <MarkdownWithFrontmatter>
        {`
---
title: Incident triage
---

Body`}
      </MarkdownWithFrontmatter>
    )

    expect(
      screen.getByRole("heading", { name: "Incident triage" })
    ).toBeInTheDocument()
    expect(screen.getByTestId("streamdown")).toHaveTextContent("Body")
  })

  it("falls back to plain markdown rendering when frontmatter is absent", () => {
    render(
      <MarkdownWithFrontmatter>{`---
owner: Platform

Body without a closing delimiter`}</MarkdownWithFrontmatter>
    )

    expect(screen.getByTestId("streamdown").textContent).toBe(
      "---\nowner: Platform\n\nBody without a closing delimiter"
    )
    expect(screen.queryByText("Raw frontmatter")).not.toBeInTheDocument()
  })

  it("rerenders when non-children props change", () => {
    const { rerender } = render(
      <MarkdownWithFrontmatter className="text-red-500">
        {`---
title: Incident triage
---

Body`}
      </MarkdownWithFrontmatter>
    )

    expect(screen.getByTestId("streamdown")).toHaveClass("text-red-500")

    rerender(
      <MarkdownWithFrontmatter className="text-blue-500">
        {`---
title: Incident triage
---

Body`}
      </MarkdownWithFrontmatter>
    )

    expect(screen.getByTestId("streamdown")).toHaveClass("text-blue-500")
    expect(screen.getByTestId("streamdown")).not.toHaveClass("text-red-500")
  })
})
