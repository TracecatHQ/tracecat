import { render, screen } from "@testing-library/react"
import type { ComponentProps } from "react"
import { WebPreview, WebPreviewBody } from "./web-preview"

function renderPreview(defaultUrl: string, props?: ComponentProps<"iframe">) {
  render(
    <WebPreview defaultUrl={defaultUrl}>
      <WebPreviewBody {...props} />
    </WebPreview>
  )

  return screen.getByTitle("Preview")
}

describe("WebPreviewBody", () => {
  it.each(["https://example.com", "http://example.com", "/relative-path"])(
    "allows safe preview URL %s",
    (url) => {
      expect(renderPreview(url)).toHaveAttribute("src", url)
    }
  )

  it("encodes HTML metacharacters in safe URLs", () => {
    expect(
      renderPreview(`https://example.com/<script>?quote='"`)
    ).toHaveAttribute("src", "https://example.com/%3Cscript%3E?quote=%27%22")
  })

  it.each(["javascript:alert(1)", "data:text/html,<script>alert(1)</script>"])(
    "blocks executable preview URL %s",
    (url) => {
      expect(renderPreview(url)).not.toHaveAttribute("src")
    }
  )

  it("does not allow iframe props to inject HTML or weaken the sandbox", () => {
    const iframe = renderPreview("https://example.com", {
      sandbox: "allow-same-origin",
      srcDoc: "<script>alert(1)</script>",
    })

    expect(iframe).not.toHaveAttribute("srcdoc")
    expect(iframe).toHaveAttribute(
      "sandbox",
      "allow-scripts allow-forms allow-popups allow-presentation"
    )
  })
})
