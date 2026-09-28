import { buildFileTree, type FileTreeNode } from "@/lib/file-tree"

interface TestFile {
  path: string
  status: string
}

function file(path: string, status = "unchanged"): TestFile {
  return { path, status }
}

function namesOf(nodes: FileTreeNode<TestFile>[]): string[] {
  return nodes.map((node) => node.name)
}

describe("buildFileTree", () => {
  it("returns an empty tree for no files", () => {
    expect(buildFileTree<TestFile>([])).toEqual([])
  })

  it("nests files under their folders", () => {
    const tree = buildFileTree([
      file("scripts/nested/run.py"),
      file("scripts/setup.sh"),
      file("README.md"),
    ])

    expect(namesOf(tree)).toEqual(["scripts", "README.md"])

    const scripts = tree[0]
    if (scripts.kind !== "folder") {
      throw new Error("expected scripts to be a folder")
    }
    expect(scripts.path).toBe("scripts")
    expect(namesOf(scripts.children)).toEqual(["nested", "setup.sh"])

    const nested = scripts.children[0]
    if (nested.kind !== "folder") {
      throw new Error("expected nested to be a folder")
    }
    expect(nested.path).toBe("scripts/nested")
    expect(namesOf(nested.children)).toEqual(["run.py"])
  })

  it("carries the original file on leaf nodes", () => {
    const source = file("config.yaml", "modified")
    const tree = buildFileTree([source])
    const node = tree[0]
    if (node.kind !== "file") {
      throw new Error("expected a file node")
    }
    expect(node.path).toBe("config.yaml")
    expect(node.file).toBe(source)
  })

  it("sorts folders before files at every level", () => {
    const tree = buildFileTree([
      file("a.md"),
      file("z-folder/inner.md"),
      file("b.md"),
    ])
    expect(namesOf(tree)).toEqual(["z-folder", "a.md", "b.md"])
  })

  it("sorts siblings of the same kind by path", () => {
    const tree = buildFileTree([file("c.md"), file("a.md"), file("b.md")])
    expect(namesOf(tree)).toEqual(["a.md", "b.md", "c.md"])
  })

  it("pins the given paths first, in the order given", () => {
    const tree = buildFileTree(
      [file("config.yaml"), file("a.md"), file("instructions.md")],
      { pinnedPaths: ["instructions.md", "config.yaml"] }
    )
    expect(namesOf(tree)).toEqual(["instructions.md", "config.yaml", "a.md"])
  })

  it("pins a path ahead of folders", () => {
    const tree = buildFileTree(
      [file("scripts/run.py"), file("instructions.md")],
      { pinnedPaths: ["instructions.md"] }
    )
    expect(namesOf(tree)).toEqual(["instructions.md", "scripts"])
  })

  it("pins only within the level that owns the path", () => {
    const tree = buildFileTree(
      [file("docs/z.md"), file("docs/a.md"), file("aaa.md")],
      { pinnedPaths: ["docs/z.md"] }
    )
    expect(namesOf(tree)).toEqual(["docs", "aaa.md"])

    const docs = tree[0]
    if (docs.kind !== "folder") {
      throw new Error("expected docs to be a folder")
    }
    expect(namesOf(docs.children)).toEqual(["z.md", "a.md"])
  })

  it("ignores pinned paths that are not present", () => {
    const tree = buildFileTree([file("b.md"), file("a.md")], {
      pinnedPaths: ["missing.md"],
    })
    expect(namesOf(tree)).toEqual(["a.md", "b.md"])
  })
})
