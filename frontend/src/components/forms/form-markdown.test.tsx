import { render, screen } from "@testing-library/react"
import {
  FormMarkdown,
  FormMarkdownLink,
} from "@/components/forms/form-markdown"
import { remarkFormMarkdown } from "@/lib/forms/markdown"

const streamdownProps = jest.fn()

jest.mock("streamdown", () => ({
  Streamdown: (props: Record<string, unknown>) => {
    streamdownProps(props)
    return <div data-testid="streamdown">{props.children as string}</div>
  },
}))

describe("FormMarkdownLink", () => {
  it.each(["https://example.com/docs", "mailto:it@example.com"])(
    "links to %s in a new tab without an opener",
    (href) => {
      render(<FormMarkdownLink href={href}>label</FormMarkdownLink>)
      const anchor = screen.getByRole("link", { name: "label" })
      expect(anchor).toHaveAttribute("href", href)
      expect(anchor).toHaveAttribute("target", "_blank")
      expect(anchor).toHaveAttribute("rel", "noopener noreferrer")
    }
  )

  it.each([
    "javascript:alert(1)",
    "data:text/html,x",
    "/workspaces",
    undefined,
  ])("renders %p as plain text", (href) => {
    render(<FormMarkdownLink href={href}>label</FormMarkdownLink>)
    expect(screen.queryByRole("link")).not.toBeInTheDocument()
    expect(screen.getByText("label")).toBeInTheDocument()
  })
})

describe("FormMarkdown", () => {
  it("filters the markdown tree before any HTML exists and shows no image", () => {
    render(<FormMarkdown>Request a **laptop**.</FormMarkdown>)

    expect(screen.getByTestId("streamdown")).toHaveTextContent(
      "Request a **laptop**."
    )
    const props = streamdownProps.mock.calls.at(-1)?.[0]
    // Remark plugins run before the raw-HTML parser Streamdown adds as a
    // rehype plugin, so dropped HTML never reaches it.
    expect(props.remarkPlugins).toEqual([remarkFormMarkdown])
    expect(props.rehypePlugins).toBeUndefined()
    expect(props.components.a).toBe(FormMarkdownLink)
    expect(props.components.img({})).toBeNull()
    expect(props.allowedImagePrefixes).toEqual([])
  })
})
