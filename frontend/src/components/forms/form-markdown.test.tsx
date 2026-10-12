import { execFileSync } from "node:child_process"
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"
import { pathToFileURL } from "node:url"
import { render, screen } from "@testing-library/react"
import { ModuleKind, ScriptTarget, transpileModule } from "typescript"
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
  it("renders through Streamdown with HTML, images and unsafe links off", () => {
    render(<FormMarkdown>Request a **laptop**.</FormMarkdown>)

    expect(screen.getByTestId("streamdown")).toHaveTextContent(
      "Request a **laptop**."
    )
    const props = streamdownProps.mock.calls.at(-1)?.[0]
    // These are the settings `tests/helpers/render-form-markdown.mjs` mirrors.
    expect(props.remarkPlugins).toEqual([remarkFormMarkdown])
    expect(props.allowedImagePrefixes).toEqual([])
    expect(props.allowedLinkPrefixes).toEqual(["*"])
    expect(props.components.a).toBe(FormMarkdownLink)
    expect(props.components.img({})).toBeNull()
    expect(props.parseIncompleteMarkdown).toBe(false)
    expect(props.controls).toBe(false)
    expect(props.rehypePlugins).toBeUndefined()
  })
})

/**
 * Jest stubs the ESM-only markdown stack, so the checks below run the real
 * one in a child Node process: the same Streamdown the app ships, with the
 * real `remarkFormMarkdown` compiled from source.
 */
describe("form markdown through the real renderer", () => {
  const cases = {
    script: "Hello <script>alert(1)</script> world",
    blockHtml: '<div onclick="alert(1)">block</div>\n\nafter',
    inlineHtml:
      "Some <b>bold</b> and <iframe src='https://example.com'></iframe>",
    imgTag: '<img src="https://example.com/x.png" onerror="alert(1)">',
    jsLink: "[click](javascript:alert(1))",
    dataLink: "[click](data:text/html,<script>alert(1)</script>)",
    image: "![alt text](https://example.com/x.png)",
    dataImage: "![alt text](data:image/png;base64,iVBORw0KGgo=)",
    refImage: "![alt][ref]\n\n[ref]: https://example.com/x.png",
    refLink: "[text][ref]\n\n[ref]: javascript:alert(1)",
    httpLink: "[docs](https://example.com/docs)",
    mailto: "[mail](mailto:it@example.com)",
    formatting: "**bold** and `code`\n\n- one\n- two",
  }
  let html: Record<keyof typeof cases, string>

  beforeAll(() => {
    const frontendRoot = resolve(__dirname, "../../..")
    const workDir = mkdtempSync(join(tmpdir(), "form-markdown-"))
    try {
      const compiled = join(workDir, "markdown.mjs")
      writeFileSync(
        compiled,
        transpileModule(
          readFileSync(join(frontendRoot, "src/lib/forms/markdown.ts"), "utf8"),
          {
            compilerOptions: {
              module: ModuleKind.ESNext,
              target: ScriptTarget.ES2022,
            },
          }
        ).outputText
      )
      html = JSON.parse(
        execFileSync(
          process.execPath,
          [
            join(frontendRoot, "tests/helpers/render-form-markdown.mjs"),
            pathToFileURL(compiled).href,
          ],
          { cwd: frontendRoot, input: JSON.stringify(cases), encoding: "utf8" }
        )
      )
    } finally {
      rmSync(workDir, { recursive: true, force: true })
    }
  }, 60_000)

  it("renders no element or attribute from raw HTML", () => {
    for (const name of [
      "script",
      "blockHtml",
      "inlineHtml",
      "imgTag",
    ] as const) {
      expect(html[name]).not.toMatch(
        /<script|<iframe|<img|<b>|onclick|onerror/i
      )
    }
    expect(html.script).toContain("Hello alert(1) world")
    expect(html.blockHtml).toContain("after")
    expect(html.blockHtml).not.toContain("block")
  })

  it("renders no image for any image syntax", () => {
    for (const name of ["image", "dataImage", "refImage"] as const) {
      expect(html[name]).not.toMatch(/<img|example\.com\/x\.png|base64/i)
    }
  })

  it("renders no anchor for javascript, data or reference links", () => {
    for (const name of ["jsLink", "dataLink", "refLink"] as const) {
      expect(html[name]).not.toMatch(/<a\b|href=|javascript:|data:text/i)
    }
    expect(html.jsLink).toContain("click")
  })

  it("keeps http(s) and mailto links and ordinary formatting", () => {
    expect(html.httpLink).toContain(
      '<a href="https://example.com/docs">docs</a>'
    )
    expect(html.mailto).toContain('<a href="mailto:it@example.com">mail</a>')
    expect(html.formatting).toMatch(/bold/)
    expect(html.formatting).toMatch(/<code[^>]*>code<\/code>/)
    expect(html.formatting).toMatch(/<li[^>]*>one<\/li>/)
  })
})
