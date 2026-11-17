/**
 * @jest-environment jsdom
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  CancelablePromise,
  getWorkspaceModelAccess,
  listCatalog,
  listEnabledModels,
  updateWorkspaceModelAccess,
  type WorkspaceAgentModelAccessRead,
  type WorkspaceRead,
} from "@/client"
import { WorkspaceModelSettings } from "@/components/settings/workspace-model-settings"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  getWorkspaceModelAccess: jest.fn(),
  updateWorkspaceModelAccess: jest.fn(),
  listCatalog: jest.fn(),
  listEnabledModels: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

const WORKSPACE = { id: "workspace-1", name: "SecOps" } as WorkspaceRead

const ACCESS: WorkspaceAgentModelAccessRead = {
  inherit_all: true,
  catalog_ids: [],
  candidates: [
    {
      id: "catalog-one",
      custom_provider_id: null,
      organization_id: null,
      model_provider: "openai",
      model_name: "model-one",
      model_metadata: {},
      lifecycle: null,
    },
    {
      id: "catalog-two",
      custom_provider_id: "provider-1",
      organization_id: "org-1",
      model_provider: "custom-model-provider",
      model_name: "model-two",
      model_metadata: {},
      lifecycle: null,
      custom_provider_name: "Synthetic gateway",
    },
  ],
}

function renderSettings() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceModelSettings workspace={WORKSPACE} />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(getWorkspaceModelAccess).mockResolvedValue(ACCESS)
  jest.mocked(updateWorkspaceModelAccess).mockImplementation(
    ({ requestBody }) =>
      new CancelablePromise<WorkspaceAgentModelAccessRead>((resolve) =>
        resolve({
          ...ACCESS,
          inherit_all: requestBody.inherit_all,
          catalog_ids: requestBody.catalog_ids ?? [],
        })
      )
  )
})

it("saves a workspace subset through the workspace route, never the org routes", async () => {
  // A workspace-only admin holds no org agent scope, so org routes would 403.
  const user = userEvent.setup()
  renderSettings()

  expect(
    await screen.findByText("Synthetic gateway", { exact: false })
  ).toBeInTheDocument()
  await user.click(screen.getByRole("switch"))
  await user.click(screen.getAllByRole("checkbox")[0])
  await user.click(screen.getByRole("button", { name: "Save" }))

  await waitFor(() => {
    expect(updateWorkspaceModelAccess).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: { inherit_all: false, catalog_ids: ["catalog-one"] },
    })
  })
  expect(getWorkspaceModelAccess).toHaveBeenCalledWith({
    workspaceId: "workspace-1",
  })
  expect(listCatalog).not.toHaveBeenCalled()
  expect(listEnabledModels).not.toHaveBeenCalled()
})
