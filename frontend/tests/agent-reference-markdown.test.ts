/** @jest-environment node */
import { execFileSync } from "node:child_process"
import path from "node:path"
import { buildSync } from "esbuild"

test("real TipTap Markdown parser agrees with backend reference fixtures", () => {
  // TipTap's marked dependency is ESM. Exercise it in Node without replacing
  // the editor parser with a Jest mock or changing the app's module settings.
  const { outputFiles } = buildSync({
    entryPoints: [path.join(__dirname, "agent-reference-conformance.ts")],
    bundle: true,
    platform: "node",
    format: "cjs",
    write: false,
  })
  expect(
    // The bundle exceeds Linux's per-argument limit; send it over stdin.
    execFileSync(process.execPath, ["--input-type=commonjs"], {
      input: outputFiles[0].text,
      encoding: "utf8",
    })
  ).toContain("TipTap Markdown conformance fixtures passed")
})
