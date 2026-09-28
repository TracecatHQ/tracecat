import { readFileSync } from "node:fs"
import { join } from "node:path"

/**
 * jsdom cannot evaluate the CSS cascade, so no component test can prove that a
 * diff surface has usable colors in dark mode. This static check on
 * `globals.scss` is what makes light-mode-only diff colors impossible to
 * reintroduce: it asserts the `.dark` block declares the same `--diff-*` token
 * set as `:root`, with its own values.
 */

const GLOBALS_SCSS_PATH = join(__dirname, "..", "src", "styles", "globals.scss")

const REQUIRED_DIFF_TOKENS = [
  "--diff-added",
  "--diff-added-emphasis",
  "--diff-added-foreground",
  "--diff-removed",
  "--diff-removed-emphasis",
  "--diff-removed-foreground",
  "--diff-marker-added",
  "--diff-marker-removed",
  "--diff-gutter",
  "--diff-gutter-foreground",
] as const

/** Background tokens whose light and dark values must not be identical. */
const BACKGROUND_TOKENS = [
  "--diff-added",
  "--diff-added-emphasis",
  "--diff-removed",
  "--diff-removed-emphasis",
  "--diff-gutter",
] as const

/**
 * Extract the body of the first `<selector> { ... }` block, counting braces so
 * nested rules do not truncate the match.
 */
function extractBlock(css: string, selector: string): string {
  const start = css.indexOf(selector)
  if (start === -1) {
    throw new Error(`Selector "${selector}" not found in globals.scss`)
  }
  const open = css.indexOf("{", start)
  if (open === -1) {
    throw new Error(`No opening brace after "${selector}" in globals.scss`)
  }
  let depth = 0
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") {
      depth += 1
    } else if (css[i] === "}") {
      depth -= 1
      if (depth === 0) {
        return css.slice(open + 1, i)
      }
    }
  }
  throw new Error(`Unbalanced braces after "${selector}" in globals.scss`)
}

/** Collect `--diff-*` custom property declarations from a CSS block body. */
function extractDiffTokens(block: string): Map<string, string> {
  const tokens = new Map<string, string>()
  const declaration = /(--diff-[a-z-]+)\s*:\s*([^;]+);/g
  let match = declaration.exec(block)
  while (match !== null) {
    tokens.set(match[1], match[2].trim())
    match = declaration.exec(block)
  }
  return tokens
}

const css = readFileSync(GLOBALS_SCSS_PATH, "utf8")
const lightTokens = extractDiffTokens(extractBlock(css, ":root"))
const darkTokens = extractDiffTokens(extractBlock(css, ".dark"))

describe("diff CSS tokens", () => {
  it("declares diff tokens in both :root and .dark", () => {
    expect(lightTokens.size).toBeGreaterThan(0)
    expect(darkTokens.size).toBeGreaterThan(0)
  })

  it("declares the same token set in both blocks", () => {
    const lightNames = [...lightTokens.keys()].sort()
    const darkNames = [...darkTokens.keys()].sort()
    expect(darkNames).toEqual(lightNames)
  })

  it.each(REQUIRED_DIFF_TOKENS)("declares %s in both blocks", (token) => {
    expect(lightTokens.has(token)).toBe(true)
    expect(darkTokens.has(token)).toBe(true)
  })

  it("gives every declared token a non-empty value", () => {
    for (const [token, value] of [...lightTokens, ...darkTokens]) {
      expect(value).not.toBe("")
      expect(token).toMatch(/^--diff-/)
    }
  })

  it.each(BACKGROUND_TOKENS)("uses a distinct dark value for %s", (token) => {
    expect(darkTokens.get(token)).not.toBe(lightTokens.get(token))
  })
})
