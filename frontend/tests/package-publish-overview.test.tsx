import { render, screen } from "@testing-library/react"
import type { PullResourceDiff } from "@/client"
import {
  PackagePublishSteps,
  PackagePublishSummary,
} from "@/components/workspace-sync/package-publish-overview"

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode
    href: string
  }) => <a href={href}>{children}</a>,
}))

function diff(
  change_type: PullResourceDiff["change_type"],
  title: string
): PullResourceDiff {
  return {
    resource_type: "workflow",
    source_id: title,
    source_path: `workflows/${title}/definition.yml`,
    change_type,
    title,
    diff: "",
  }
}

describe("PackagePublishSteps", () => {
  it("says publishing changes no customer and links to the deploy page", () => {
    render(<PackagePublishSteps branch="package/update" />)

    expect(screen.getByText("package/update")).toBeInTheDocument()
    expect(
      screen.getByText(/No customer workspace changes/)
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "Admin, Managed package" })
    ).toHaveAttribute("href", "/admin/managed-package")
  })
})

describe("PackagePublishSummary", () => {
  it("lists what is added, updated and removed by name", () => {
    render(
      <PackagePublishSummary
        compareRef="main"
        diffs={[
          diff("added", "triage"),
          diff("modified", "enrich"),
          diff("deleted", "legacy"),
        ]}
      />
    )

    expect(screen.getByText("Added to the package (1)")).toBeInTheDocument()
    expect(screen.getByText("Updated in the package (1)")).toBeInTheDocument()
    expect(screen.getByText("Removed from the package (1)")).toBeInTheDocument()
    expect(screen.getByText("legacy")).toBeInTheDocument()
  })

  it("lists a resource with several changed files once", () => {
    render(
      <PackagePublishSummary
        compareRef="main"
        diffs={[
          diff("modified", "triage"),
          {
            ...diff("added", "triage"),
            source_path: "workflows/triage/layout.yml",
          },
          diff("added", "enrich"),
          {
            ...diff("added", "enrich"),
            source_path: "workflows/enrich/layout.yml",
          },
        ]}
      />
    )

    expect(screen.getByText("Updated in the package (1)")).toBeInTheDocument()
    expect(screen.getByText("Added to the package (1)")).toBeInTheDocument()
    expect(screen.getAllByText("triage")).toHaveLength(1)
    expect(screen.getAllByText("enrich")).toHaveLength(1)
  })

  it("says nothing changes when the branch already matches", () => {
    render(<PackagePublishSummary compareRef="main" diffs={[]} />)

    expect(
      screen.getByText(/already matches your selection/)
    ).toBeInTheDocument()
  })
})
