/** Run with esbuild + Node so the real TipTap/marked ESM parser is exercised. */
import assert from "node:assert/strict"
import fixtures from "../../tests/fixtures/agent_references/conformance.json"
import { parseMarkdownReferences } from "../src/lib/agent-reference-markdown"
import { serializeReferenceURI } from "../src/lib/agent-reference-uri"

for (const fixture of fixtures.markdown) {
  const result = parseMarkdownReferences(fixture.source)
  assert.deepEqual(
    result.references.map(serializeReferenceURI),
    fixture.targets,
    fixture.name
  )
  assert.deepEqual(result.diagnostics, fixture.errors, fixture.name)
}
console.log(
  `${fixtures.markdown.length} TipTap Markdown conformance fixtures passed`
)
