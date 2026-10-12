// Renders markdown through the real Streamdown pipeline with the settings
// `FormMarkdown` uses, and prints the HTML.
//
// Jest replaces `streamdown` and `react-markdown` with stubs because they are
// ESM-only, so a Jest test cannot see what form markdown actually renders.
// `form-markdown.test.tsx` runs this script in a child Node process instead.
//
// Usage: node render-form-markdown.mjs <url of the compiled lib/forms/markdown>
// stdin:  JSON object of { name: markdown }
// stdout: JSON object of { name: html }
import { readFileSync } from "node:fs"
import { register } from "node:module"

// Streamdown imports the KaTeX stylesheet, which Node cannot load.
register(
  `data:text/javascript,${encodeURIComponent(
    'export async function load(url, context, nextLoad) { if (url.endsWith(".css")) return { format: "module", source: "", shortCircuit: true }; return nextLoad(url, context) }'
  )}`
)

const { createElement } = await import("react")
const { renderToStaticMarkup } = await import("react-dom/server")
const { Streamdown } = await import("streamdown")
const { isSafeFormLink, remarkFormMarkdown } = await import(process.argv[2])

// Mirrors `FormMarkdownLink`.
function Link({ href, children }) {
  return isSafeFormLink(href)
    ? createElement("a", { href }, children)
    : createElement("span", null, children)
}

// Mirrors the props `FormMarkdown` passes. The Jest test asserts those props,
// so the two cannot drift apart unnoticed.
function render(markdown) {
  return renderToStaticMarkup(
    createElement(
      Streamdown,
      {
        parseIncompleteMarkdown: false,
        controls: false,
        remarkPlugins: [remarkFormMarkdown],
        components: { a: Link, img: () => null },
        allowedLinkPrefixes: ["*"],
        allowedImagePrefixes: [],
        defaultOrigin: "https://tracecat.local",
      },
      markdown
    )
  )
}

const cases = JSON.parse(readFileSync(0, "utf8"))
process.stdout.write(
  JSON.stringify(
    Object.fromEntries(
      Object.entries(cases).map(([name, markdown]) => [name, render(markdown)])
    )
  )
)
