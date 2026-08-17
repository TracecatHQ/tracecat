import type { VersionFileFingerprint } from "@/components/version-history/types"
import { compareVersionManifests } from "@/lib/version-diff"

function fingerprint(path: string, value: string): VersionFileFingerprint {
  return { path, fingerprint: value }
}

describe("compareVersionManifests", () => {
  it("labels a path only in the version as added", () => {
    const entries = compareVersionManifests([], [fingerprint("a.md", "sha-a")])
    expect(entries).toEqual([{ path: "a.md", status: "added" }])
  })

  it("labels a path only in the draft as removed", () => {
    const entries = compareVersionManifests([fingerprint("a.md", "sha-a")], [])
    expect(entries).toEqual([{ path: "a.md", status: "removed" }])
  })

  it("labels differing fingerprints as modified", () => {
    const entries = compareVersionManifests(
      [fingerprint("a.md", "sha-draft")],
      [fingerprint("a.md", "sha-version")]
    )
    expect(entries).toEqual([{ path: "a.md", status: "modified" }])
  })

  it("labels equal sha256 fingerprints as unchanged", () => {
    const sha =
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    const entries = compareVersionManifests(
      [fingerprint("a.md", sha)],
      [fingerprint("a.md", sha)]
    )
    expect(entries).toEqual([{ path: "a.md", status: "unchanged" }])
  })

  it("sorts the result by path", () => {
    const entries = compareVersionManifests(
      [fingerprint("z.md", "sha-z"), fingerprint("a/b.md", "sha-b")],
      [fingerprint("m.md", "sha-m"), fingerprint("a/b.md", "sha-b")]
    )
    expect(entries.map((entry) => entry.path)).toEqual([
      "a/b.md",
      "m.md",
      "z.md",
    ])
  })

  it("marks everything added when the draft is empty", () => {
    const entries = compareVersionManifests(
      [],
      [fingerprint("b.md", "sha-b"), fingerprint("a.md", "sha-a")]
    )
    expect(entries).toEqual([
      { path: "a.md", status: "added" },
      { path: "b.md", status: "added" },
    ])
  })

  it("marks everything removed when the version is empty", () => {
    const entries = compareVersionManifests(
      [fingerprint("b.md", "sha-b"), fingerprint("a.md", "sha-a")],
      []
    )
    expect(entries).toEqual([
      { path: "a.md", status: "removed" },
      { path: "b.md", status: "removed" },
    ])
  })

  it("returns nothing when both manifests are empty", () => {
    expect(compareVersionManifests([], [])).toEqual([])
  })

  it("resolves a mixed manifest", () => {
    const entries = compareVersionManifests(
      [
        fingerprint("config.yaml", "draft-config"),
        fingerprint("instructions.md", "same"),
        fingerprint("only-draft.md", "draft-only"),
      ],
      [
        fingerprint("config.yaml", "version-config"),
        fingerprint("instructions.md", "same"),
        fingerprint("only-version.md", "version-only"),
      ]
    )
    expect(entries).toEqual([
      { path: "config.yaml", status: "modified" },
      { path: "instructions.md", status: "unchanged" },
      { path: "only-draft.md", status: "removed" },
      { path: "only-version.md", status: "added" },
    ])
  })
})
