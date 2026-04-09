import {
  composeMarkdownFrontmatter,
  splitMarkdownFrontmatter,
} from "@/lib/markdown-frontmatter"
import { getLanguageForPath } from "@/lib/skills-studio"

describe("skills studio markdown editor selection", () => {
  it("splits and recomposes a frontmatter document", () => {
    const split = splitMarkdownFrontmatter(`---
title: Incident triage
---

Body`)

    expect(split).not.toBeNull()
    expect(split?.frontmatter).toBe("title: Incident triage")
    expect(split?.body.trim()).toBe("Body")
    expect(
      composeMarkdownFrontmatter(split?.frontmatter ?? "", "Updated body")
    ).toBe(`---
title: Incident triage
---

Updated body`)
  })

  it("keeps language selection for non-markdown code files", () => {
    expect(getLanguageForPath("script.py")).toBe("python")
  })
})
