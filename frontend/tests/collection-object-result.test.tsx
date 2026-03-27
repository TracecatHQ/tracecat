/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import type React from "react"
import { workflowExecutionsGetWorkflowExecutionCollectionPage } from "@/client"
import { CollectionObjectResult } from "@/components/executions/collection-object-result"
import {
  isExternalStoredObject,
  isInlineStoredObject,
} from "@/lib/stored-object"

jest.mock("@/client", () => ({
  workflowExecutionsGetWorkflowExecutionCollectionPage: jest.fn(),
}))

jest.mock("@/components/code-block", () => ({
  CodeBlock: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}))

jest.mock("@/components/executions/external-object-result", () => ({
  ExternalObjectResult: () => null,
}))

jest.mock("@/components/json-viewer", () => ({
  JsonViewWithControls: ({
    src,
    copyMode,
    copyPrefix,
  }: {
    src: unknown
    copyMode?: string
    copyPrefix?: string
  }) => (
    <div
      data-testid="json-view"
      data-copy-mode={copyMode}
      data-copy-prefix={copyPrefix}
    >
      {JSON.stringify(src)}
    </div>
  ),
}))

jest.mock("@/lib/stored-object", () => ({
  isExternalStoredObject: jest.fn(() => false),
  isInlineStoredObject: jest.fn(() => true),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))

const mockGetCollectionPage =
  workflowExecutionsGetWorkflowExecutionCollectionPage as jest.MockedFunction<
    typeof workflowExecutionsGetWorkflowExecutionCollectionPage
  >
const mockIsExternalStoredObject =
  isExternalStoredObject as jest.MockedFunction<typeof isExternalStoredObject>
const mockIsInlineStoredObject = isInlineStoredObject as jest.MockedFunction<
  typeof isInlineStoredObject
>

describe("CollectionObjectResult", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockIsExternalStoredObject.mockReturnValue(false)
    mockIsInlineStoredObject.mockReturnValue(true)
    mockGetCollectionPage.mockResolvedValue({
      collection: {
        count: 1,
        chunk_size: 25,
        element_kind: "stored_object",
      },
      items: [
        {
          index: 0,
          kind: "stored_object_ref",
          stored: {
            data: { foo: "bar" },
          },
          value_size_bytes: 32,
          truncated: false,
        },
      ],
      next_offset: null,
    } as never)
  })

  it("passes the dual copy mode to inline JSON previews", async () => {
    render(
      <CollectionObjectResult
        executionId="exec-1"
        eventId={1}
        collection={
          {
            count: 1,
            chunk_size: 25,
            element_kind: "stored_object",
          } as never
        }
        copyMode="jsonpath-and-payload"
        copyPrefix="ACTIONS.reshape.result"
      />
    )

    await waitFor(() => {
      expect(screen.getByTestId("json-view")).toHaveAttribute(
        "data-copy-mode",
        "jsonpath-and-payload"
      )
      expect(screen.getByTestId("json-view")).toHaveAttribute(
        "data-copy-prefix",
        "ACTIONS.reshape.result[0]"
      )
    })
  })
})
