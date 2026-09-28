import { render, screen } from "@testing-library/react"
import { InlineDiffView } from "@/components/diff/inline-diff-view"

/**
 * `oldValue` is the current draft and `newValue` is the selected historical
 * version, so added = text that comes back if you restore that version and
 * removed = draft text that restoring would lose.
 */
describe("InlineDiffView", () => {
  describe("mode selection", () => {
    it("renders markdown paths as prose", () => {
      render(
        <InlineDiffView
          path="notes.md"
          oldValue="Hello world"
          newValue="Hello brave world"
        />
      )
      expect(screen.getByTestId("prose-diff")).toBeInTheDocument()
      expect(screen.queryByTestId("unified-diff")).not.toBeInTheDocument()
    })

    it("renders code paths as a unified diff", () => {
      render(
        <InlineDiffView
          path="config.yaml"
          oldValue={"a: 1\nb: 2"}
          newValue={"a: 1\nb: 3"}
        />
      )
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument()
      expect(screen.queryByTestId("prose-diff")).not.toBeInTheDocument()
    })

    it("lets an explicit mode prop override path-based resolution", () => {
      render(
        <InlineDiffView
          path="notes.md"
          oldValue="Hello world"
          newValue="Hello brave world"
          mode="unified"
        />
      )
      expect(screen.getByTestId("unified-diff")).toBeInTheDocument()
      expect(screen.queryByTestId("prose-diff")).not.toBeInTheDocument()
    })
  })

  describe("diff direction", () => {
    it("marks text present only in the historical version as added", () => {
      const { container } = render(
        <InlineDiffView
          path="notes.md"
          oldValue="The quick fox"
          newValue="The quick brown fox"
        />
      )
      const added = container.querySelector('ins[data-diff="added"]')
      expect(added).not.toBeNull()
      expect(added?.textContent).toContain("brown")
      expect(container.querySelector('del[data-diff="removed"]')).toBeNull()
    })

    it("marks draft text absent from the historical version as removed", () => {
      const { container } = render(
        <InlineDiffView
          path="notes.md"
          oldValue="The quick brown fox"
          newValue="The quick fox"
        />
      )
      const removed = container.querySelector('del[data-diff="removed"]')
      expect(removed).not.toBeNull()
      expect(removed?.textContent).toContain("brown")
      expect(container.querySelector('ins[data-diff="added"]')).toBeNull()
    })
  })

  describe("empty states", () => {
    it("shows the open-file pattern when neither side is previewable", () => {
      render(
        <InlineDiffView
          path="logo.png"
          oldValue={null}
          newValue={null}
          downloadUrl="https://example.com/files/logo.png"
        />
      )
      expect(
        screen.getByText("This file is not previewable inline.")
      ).toBeInTheDocument()
      const link = screen.getByRole("link", { name: "Open file" })
      expect(link).toHaveAttribute("href", "https://example.com/files/logo.png")
    })

    it("shows the both-sides-absent copy when there is nothing to open", () => {
      render(<InlineDiffView path="ghost.md" oldValue={null} newValue={null} />)
      expect(
        screen.getByText(
          "This file does not exist in the draft or in this version."
        )
      ).toBeInTheDocument()
    })

    it("notes a file added by the version and still shows its content as added", () => {
      const { container } = render(
        <InlineDiffView
          path="fresh.md"
          oldValue={null}
          newValue="Fresh content"
        />
      )
      expect(
        screen.getByText("Restoring this version adds this file.")
      ).toBeInTheDocument()
      const added = container.querySelector('ins[data-diff="added"]')
      expect(added?.textContent).toContain("Fresh content")
    })

    it("notes a file removed by the version and still shows its content as removed", () => {
      const { container } = render(
        <InlineDiffView
          path="stale.md"
          oldValue="Old content"
          newValue={null}
        />
      )
      expect(
        screen.getByText("Restoring this version removes this file.")
      ).toBeInTheDocument()
      const removed = container.querySelector('del[data-diff="removed"]')
      expect(removed?.textContent).toContain("Old content")
    })

    it("shows the no-changes copy when both sides are identical", () => {
      render(
        <InlineDiffView
          path="same.md"
          oldValue="Same text"
          newValue="Same text"
        />
      )
      expect(
        screen.getByText(
          "No changes. Restoring this version leaves this file unchanged."
        )
      ).toBeInTheDocument()
      expect(screen.queryByTestId("prose-diff")).not.toBeInTheDocument()
    })

    it("shows the too-large copy instead of diffing oversized files", () => {
      render(
        <InlineDiffView
          path="huge.txt"
          oldValue={"x".repeat(1_000_001)}
          newValue="y"
        />
      )
      expect(
        screen.getByText("This file is too large to compare.")
      ).toBeInTheDocument()
      expect(screen.queryByTestId("prose-diff")).not.toBeInTheDocument()
      expect(screen.queryByTestId("unified-diff")).not.toBeInTheDocument()
    })
  })

  describe("markdown frontmatter", () => {
    it("renders frontmatter and body as stacked sections when both sides have frontmatter", () => {
      render(
        <InlineDiffView
          path="skill.md"
          oldValue={"---\ntitle: Old title\n---\n\nBody one."}
          newValue={"---\ntitle: New title\n---\n\nBody two."}
        />
      )
      expect(screen.getByText("Frontmatter")).toBeInTheDocument()
      expect(screen.getByTestId("unified-diff")).toHaveTextContent("title:")
      expect(screen.getByTestId("prose-diff")).toHaveTextContent("Body")
    })

    it("falls back to whole-document prose when only one side has frontmatter", () => {
      render(
        <InlineDiffView
          path="skill.md"
          oldValue="Body one."
          newValue={"---\ntitle: New title\n---\n\nBody two."}
        />
      )
      expect(screen.queryByText("Frontmatter")).not.toBeInTheDocument()
      expect(screen.queryByTestId("unified-diff")).not.toBeInTheDocument()
      expect(screen.getByTestId("prose-diff")).toBeInTheDocument()
    })
  })

  describe("unified rendering", () => {
    it("collapses long unchanged runs into a hidden-lines row", () => {
      const shared = Array.from({ length: 10 }, (_, i) => `line ${i}`)
      render(
        <InlineDiffView
          path="config.yaml"
          oldValue={["first: old", ...shared].join("\n")}
          newValue={["first: new", ...shared].join("\n")}
        />
      )
      expect(screen.getByText("4 unchanged lines hidden")).toBeInTheDocument()
    })
  })

  it("renders with no ThemeProvider mounted", () => {
    // The diff components must stay free of useTheme/resolvedTheme: that hook
    // is undefined on first client render and would flash the light palette.
    // Rendering here without any provider proves there is no theme-hook
    // coupling — theming comes entirely from the --diff-* CSS tokens.
    const { container } = render(
      <InlineDiffView
        path="notes.md"
        oldValue="Hello world"
        newValue="Hello brave world"
      />
    )
    expect(screen.getByTestId("prose-diff")).toBeInTheDocument()
    expect(container.querySelector('ins[data-diff="added"]')).not.toBeNull()
  })
})
