/** @jest-environment jsdom */
import { render, screen } from "@testing-library/react"
import {
  ExportErrorMessage,
  getExportError,
} from "@/components/workspace-sync/export-error"

import { GitSyncNotice } from "@/components/workspace-sync/git-sync-change-list"

function apiError(body: unknown) {
  return Object.assign(new Error("Request failed"), { body })
}

test("renders legacy string errors", () => {
  render(
    <ExportErrorMessage
      workspaceId="example"
      error={apiError({ detail: "Pick another branch" })}
    />
  )
  expect(screen.getByText("Pick another branch")).toBeInTheDocument()
})

test("workflow and workspace pushes show distinct editor links for duplicate titles", () => {
  const ids = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
  ]
  const error = apiError({
    detail: "Push failed",
    omitted_count: 3,
    diagnostics: ids.map((workflow_id) => ({
      workflow_id,
      workflow_title: "Example review",
      stage: "definition",
      code: "no_actions",
      message: "Add an action before pushing.",
    })),
  })
  render(<ExportErrorMessage workspaceId="example" error={error} />)
  const links = screen.getAllByRole("link", { name: "Example review" })
  expect(links[0]).toHaveAttribute(
    "href",
    `/workspaces/example/workflows/${ids[0]}`
  )
  expect(links[1]).toHaveAttribute(
    "href",
    `/workspaces/example/workflows/${ids[1]}`
  )
  expect(screen.getByText("3 additional error(s) omitted.")).toBeInTheDocument()
})

test("rejects invalid link targets and falls back to legacy detail", () => {
  const error = apiError({
    detail: "Invalid workflow",
    diagnostics: [
      {
        workflow_id: "https://example.com",
        workflow_title: "Example",
        message: "Fix",
      },
    ],
  })
  expect(getExportError(error)).toBeNull()
  render(<ExportErrorMessage workspaceId="example" error={error} />)
  expect(screen.queryByRole("link")).not.toBeInTheDocument()
  expect(screen.getByText("Invalid workflow")).toBeInTheDocument()
})

const validDiagnostic = {
  workflow_id: "00000000-0000-4000-8000-000000000001",
  workflow_title: "Synthetic workflow",
  stage: "definition",
  code: "no_actions",
  message: "Add an action before pushing.",
}

test.each([
  null,
  {},
  new Error(),
  apiError({ detail: "Legacy failure" }),
  apiError({ diagnostics: {} }),
])("ignores an error without usable diagnostics: %p", (error) =>
  expect(getExportError(error)).toBeNull()
)

test.each([
  { workflow_id: "https://example.com" },
  { workflow_title: null },
  { workflow_title: 42 },
  { message: null },
])(
  "filters malformed diagnostics while retaining valid siblings: %p",
  (invalid) => {
    expect(
      getExportError(
        apiError({
          detail: "Synthetic failure",
          diagnostics: [{ ...validDiagnostic, ...invalid }, validDiagnostic],
        })
      )
    ).toEqual({
      detail: "Synthetic failure",
      diagnostics: [validDiagnostic],
      omitted_count: 0,
    })
  }
)

test("preview notice announces the diagnostic list once", () => {
  render(
    <GitSyncNotice tone="error">
      Could not preview this push:
      <ExportErrorMessage
        announce={false}
        workspaceId="synthetic"
        error={apiError({ diagnostics: [validDiagnostic] })}
      />
    </GitSyncNotice>
  )
  expect(screen.getAllByRole("alert")).toHaveLength(1)
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Add an action before pushing."
  )
})
