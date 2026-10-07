import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import type { ComponentProps, ReactNode } from "react"
import {
  type EmbeddingConfigurationRead,
  searchGetEmbeddingConfiguration,
  type TableColumnRead,
  type TableSearchConfiguration,
  tablesGetTableSearch,
  tablesGetTableSearchProgress,
  tablesRetryTableSearch,
  tablesSelectTableSearchColumn,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AgGridColumnHeader } from "@/components/tables/ag-grid-column-header"
import { TableSearchBadge } from "@/components/tables/table-search-badge"
import { TableSearchProvider } from "@/components/tables/table-search-context"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import { toast } from "@/components/ui/use-toast"
import { invalidateArtifactQueries } from "@/components/workspace-chat/artifacts/artifact-registry"
import { searchPollInterval, tableSearchKey } from "@/hooks/use-table-search"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  searchGetEmbeddingConfiguration: jest.fn(),
  tablesGetTableSearch: jest.fn(),
  tablesSelectTableSearchColumn: jest.fn(),
  tablesGetTableSearchProgress: jest.fn(),
  tablesRetryTableSearch: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({ useScopeCheck: jest.fn() }))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-synthetic",
}))
jest.mock("next/navigation", () => ({
  useParams: () => ({ tableId: "table-synthetic" }),
}))
jest.mock("@/lib/hooks", () => ({
  useGetTable: () => ({
    table: {
      columns: [column, { ...column, id: "summary-id", name: "summary" }],
    },
  }),
  useDeleteColumn: () => ({ deleteColumn: jest.fn() }),
  useUpdateColumn: () => ({ updateColumn: jest.fn() }),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

const mockHasEntitlement = jest.fn()
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({
    hasEntitlement: (key: string) => mockHasEntitlement(key),
    isLoading: false,
    hasEntitlementData: true,
  }),
}))

const ENABLE = "Enable vector search"
const DISABLE = "Disable vector search"
const RETRY = "Retry failed rows on this page"

const column: TableColumnRead = {
  id: "body-id",
  name: "body",
  type: "TEXT",
  is_index: true,
}
const available: EmbeddingConfigurationRead = {
  available: true,
  version: 1,
  state: "active",
  reindex_required: false,
  configuration: {
    provider: "openai",
    model: "text-embedding-3-small",
    dimensions: 1536,
    tokenizer: "synthetic",
    input_token_limit: 8191,
    input_character_limit: 131072,
    batch_size_limit: 32,
    batch_token_limit: 16000,
  },
}
const failedRow = {
  document_id: "doc-synthetic",
  row_id: "row-synthetic",
  state: "failed" as const,
  revision: 1,
  expected_chunks: 1,
  sampled_chunks: 1,
  sampled_embedded: 0,
  chunks_capped: false,
  error_code: "TIMEOUT" as const,
}
let configuration: TableSearchConfiguration
let permissions: Set<string>
let client: QueryClient

function provide(children: ReactNode) {
  return (
    <TableSearchProvider tableId="table-synthetic">
      {children}
    </TableSearchProvider>
  )
}

function setup(children: ReactNode) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

function menus(columns = [column]) {
  return provide(
    columns.map((item) => <TableViewColumnMenu key={item.id} column={item} />)
  )
}

function openMenu(position = 0) {
  fireEvent.keyDown(
    screen.getAllByRole("button", { name: "Configure column" })[position],
    { key: "ArrowDown" }
  )
}

/** Open a column menu and wait for its vector search item to be usable. */
async function chooseSearchItem(name: string, position = 0) {
  openMenu(position)
  const item = await screen.findByRole("menuitem", { name })
  await waitFor(() => expect(item).not.toHaveAttribute("data-disabled"))
  fireEvent.click(item)
  return screen.findByRole("alertdialog")
}

function openDetails() {
  fireEvent.pointerEnter(screen.getByLabelText(/^Semantic search:/))
}

beforeEach(() => {
  jest.clearAllMocks()
  mockHasEntitlement.mockImplementation((key) => key === "semantic_search")
  permissions = new Set([
    "workspace:read",
    "table:read",
    "table:update",
    "table:delete",
  ])
  jest
    .mocked(useScopeCheck)
    .mockImplementation((scope) => permissions.has(scope ?? ""))
  configuration = {
    generation: 2,
    selected_column_ids: [],
    status: "disabled",
    index: {
      state: "active",
      ready: 0,
      pending: 0,
      failed: 0,
      empty: 0,
      backfill_complete: false,
      partial: true,
    },
  }
  jest
    .mocked(tablesGetTableSearch)
    .mockImplementation(
      () =>
        Promise.resolve(configuration) as ReturnType<
          typeof tablesGetTableSearch
        >
    )
  jest.mocked(searchGetEmbeddingConfiguration).mockResolvedValue(available)
  jest
    .mocked(tablesGetTableSearchProgress)
    .mockResolvedValue({ generation: 2, items: [], has_more: false })
  jest.mocked(tablesRetryTableSearch).mockResolvedValue(undefined)
})
afterEach(() => client?.clear())

test("read permissions gate all authenticated status requests", async () => {
  permissions.delete("workspace:read")
  setup(
    <>
      {provide(<TableSearchBadge />)}
      {menus()}
    </>
  )
  expect(tablesGetTableSearch).not.toHaveBeenCalled()
  expect(searchGetEmbeddingConfiguration).not.toHaveBeenCalled()
  expect(screen.queryByLabelText(/Semantic search/)).not.toBeInTheDocument()
  openMenu()
  await screen.findByText("Remove unique index")
  expect(screen.queryByText(/vector search/)).not.toBeInTheDocument()
})

test("without the entitlement the badge opens the Enterprise only dialog", async () => {
  mockHasEntitlement.mockReturnValue(false)
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByLabelText("Semantic search: Enterprise only")
  fireEvent.pointerEnter(badge)
  expect(screen.queryByText(/Model:/)).not.toBeInTheDocument()
  fireEvent.click(badge)
  const dialog = await screen.findByRole("dialog")
  expect(dialog).toHaveTextContent("Enterprise only")
  expect(dialog).toHaveTextContent(
    "Semantic search is only available on enterprise plans."
  )
  expect(tablesGetTableSearch).not.toHaveBeenCalled()
  expect(searchGetEmbeddingConfiguration).not.toHaveBeenCalled()
})

test("without the entitlement the column menu item opens the Enterprise only dialog", async () => {
  mockHasEntitlement.mockReturnValue(false)
  permissions.delete("table:update")
  setup(menus())
  openMenu()
  const item = await screen.findByRole("menuitem", { name: ENABLE })
  expect(item).not.toHaveAttribute("data-disabled")
  fireEvent.click(item)
  const dialog = await screen.findByRole("dialog")
  expect(dialog).toHaveTextContent("Enterprise only")
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  expect(tablesSelectTableSearchColumn).not.toHaveBeenCalled()
  expect(tablesGetTableSearch).not.toHaveBeenCalled()
  expect(searchGetEmbeddingConfiguration).not.toHaveBeenCalled()
})

test("read-only users see the status but cannot change selection or retry", async () => {
  permissions.delete("table:update")
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "needs_attention",
    index: { ...configuration.index, state: "active", failed: 1 },
  }
  setup(
    <>
      {provide(<TableSearchBadge />)}
      {menus()}
    </>
  )
  await screen.findByLabelText("Semantic search: Needs attention")
  openDetails()
  await screen.findByText(/0 ready · 0 pending · 1 failed · 0 empty/)
  expect(tablesGetTableSearchProgress).not.toHaveBeenCalled()
  expect(screen.queryByRole("button", { name: RETRY })).not.toBeInTheDocument()
  openMenu()
  expect(
    await screen.findByRole("menuitem", { name: DISABLE })
  ).toHaveAttribute("data-disabled")
})

test("missing provider disables enabling and keeps existing table controls", async () => {
  jest.mocked(searchGetEmbeddingConfiguration).mockResolvedValue({
    available: false,
    version: 0,
    state: "disabled",
    configuration: null,
  })
  setup(
    <>
      {provide(<TableSearchBadge />)}
      {menus()}
    </>
  )
  await screen.findByLabelText("Semantic search: Unavailable")
  openDetails()
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "No embedding provider is configured."
  )
  openMenu()
  await screen.findByText("Remove unique index")
  expect(screen.getByText("Delete column")).toBeInTheDocument()
  const item = screen.getByRole("menuitem", { name: ENABLE })
  expect(item).toHaveAttribute("data-disabled")
  fireEvent.click(item)
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
})

test("non-text columns have no vector search item", async () => {
  setup(menus([{ ...column, type: "INTEGER" }]))
  await waitFor(() => expect(tablesGetTableSearch).toHaveBeenCalled())
  openMenu()
  await screen.findByText("Remove unique index")
  expect(screen.queryByText(/vector search/)).not.toBeInTheDocument()
})

test("the badge shows a spinner only while work is pending and details on hover", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "indexing",
    index: { ...configuration.index, state: "active", ready: 3, pending: 2 },
  }
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByLabelText("Semantic search: Indexing")
  expect(badge.querySelector("svg")).toHaveClass("animate-spin")
  openDetails()
  await screen.findByText("Model: openai / text-embedding-3-small")
  expect(screen.getByText("Columns: body")).toBeInTheDocument()
  expect(
    screen.getByText("3 ready · 2 pending · 0 failed · 0 empty")
  ).toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(tablesGetTableSearchProgress).not.toHaveBeenCalled()

  configuration = {
    ...configuration,
    status: "ready",
    index: { ...configuration.index, state: "active", ready: 5, pending: 0 },
  }
  await act(async () => {
    await client.invalidateQueries({
      queryKey: tableSearchKey("workspace-synthetic", "table-synthetic"),
    })
  })
  await waitFor(() => expect(badge).toHaveTextContent("1 semantic"))
  expect(badge.querySelector("svg")).not.toHaveClass("animate-spin")
  expect(badge).not.toHaveClass("bg-green-100")
})

test("the badge counts selected columns and skips unresolved names", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id, "summary-id", "missing-id"],
    status: "ready",
  }
  setup(provide(<TableSearchBadge />))
  expect(
    await screen.findByLabelText("Semantic search: Ready")
  ).toHaveTextContent("3 semantic")
  openDetails()
  expect(await screen.findByText("Columns: body, summary")).toBeInTheDocument()
})

test("the column header shows Semantic next to Index for a selected column", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  const props = {
    tableColumn: column,
    displayName: column.name,
    column: { getSort: () => null },
  } as ComponentProps<typeof AgGridColumnHeader>
  setup(provide(<AgGridColumnHeader {...props} />))
  const semantic = await screen.findByText("Semantic")
  expect(screen.getByText("Index")).toBeInTheDocument()
  expect(semantic).toHaveClass("bg-primary/10", "text-primary")
  expect(semantic.querySelector("svg")).toHaveClass("mr-1", "size-3")
})

test("opening the badge without a pointer moves focus into the retry controls", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "needs_attention",
    index: {
      state: "active",
      ready: 0,
      pending: 0,
      failed: 1,
      empty: 0,
      backfill_complete: true,
      partial: true,
    },
  }
  jest
    .mocked(tablesGetTableSearchProgress)
    .mockResolvedValue({ generation: 2, items: [failedRow] })
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByRole("button", {
    name: "Semantic search: Needs attention",
  })
  fireEvent.click(badge)
  const retry = await screen.findByRole("button", { name: RETRY })
  expect(screen.getByRole("dialog")).toContainElement(retry)
  await waitFor(() =>
    expect(screen.getByRole("dialog")).toContainElement(
      document.activeElement as HTMLElement
    )
  )
  fireEvent.click(badge)
  expect(retry).toBeInTheDocument()
})

test("an unselected table reads Off with no index counts", async () => {
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByLabelText("Semantic search: Off")
  expect(badge).toHaveClass("text-muted-foreground")
  openDetails()
  await screen.findByText("Model: openai / text-embedding-3-small")
  expect(screen.queryByText(/ready ·/)).not.toBeInTheDocument()
  expect(screen.queryByText(/^Columns:/)).not.toBeInTheDocument()
})

test("two text selections coexist with uniqueness and use confirmed generations", async () => {
  jest
    .mocked(tablesSelectTableSearchColumn)
    .mockImplementation(({ requestBody }) => {
      configuration = {
        ...configuration,
        generation: (configuration.generation ?? 0) + 1,
        selected_column_ids: [
          ...(configuration.selected_column_ids ?? []),
          requestBody.column_id,
        ],
        status: "indexing",
      }
      return Promise.resolve(configuration) as ReturnType<
        typeof tablesSelectTableSearchColumn
      >
    })
  setup(
    menus([
      column,
      { ...column, id: "summary-id", name: "summary", is_index: false },
    ])
  )
  for (const position of [0, 1]) {
    const dialog = await chooseSearchItem(ENABLE, position)
    expect(tablesSelectTableSearchColumn).toHaveBeenCalledTimes(position)
    fireEvent.click(within(dialog).getByRole("button", { name: ENABLE }))
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
    )
    openMenu(position)
    await screen.findByRole("menuitem", { name: DISABLE })
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
    await waitFor(() =>
      expect(screen.queryByRole("menu")).not.toBeInTheDocument()
    )
  }
  expect(tablesSelectTableSearchColumn).toHaveBeenNthCalledWith(2, {
    workspaceId: "workspace-synthetic",
    tableId: "table-synthetic",
    requestBody: {
      column_id: "summary-id",
      enabled: true,
      expected_generation: 3,
    },
  })
  expect(toast).toHaveBeenCalledTimes(2)
  expect(toast).toHaveBeenLastCalledWith(
    expect.objectContaining({ title: "Enabled vector search" })
  )
  expect(column.is_index).toBe(true)
})

test("the confirm dialogs name the column and the embedding destination", async () => {
  configuration = { ...configuration, selected_column_ids: ["summary-id"] }
  setup(
    menus([
      column,
      { ...column, id: "summary-id", name: "summary", is_index: false },
    ])
  )
  const enable = await chooseSearchItem(ENABLE, 0)
  expect(enable).toHaveTextContent(
    "Text in column body will be indexed for vector search. Text is sent to openai / text-embedding-3-small."
  )
  fireEvent.click(within(enable).getByRole("button", { name: "Cancel" }))
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  )
  const disable = await chooseSearchItem(DISABLE, 1)
  expect(disable).toHaveTextContent(
    "Column summary is removed from vector search and the index is rebuilt for the remaining columns."
  )
  expect(disable).not.toHaveTextContent("openai")
  expect(tablesSelectTableSearchColumn).not.toHaveBeenCalled()
})

test("conflicts refresh and preserve confirmed selection without a success toast", async () => {
  jest.mocked(tablesSelectTableSearchColumn).mockRejectedValue({ status: 409 })
  setup(menus())
  const dialog = await chooseSearchItem(ENABLE)
  fireEvent.click(within(dialog).getByRole("button", { name: ENABLE }))
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Search settings changed" })
    )
  )
  await waitFor(() => expect(tablesGetTableSearch).toHaveBeenCalledTimes(2))
  expect(toast).toHaveBeenCalledTimes(1)
  expect(
    await within(dialog).findByRole("button", { name: ENABLE })
  ).toBeEnabled()
})

test("the confirm shows a loading state while its write is pending and a failure keeps confirmed state", async () => {
  let rejectWrite: (error: unknown) => void = () => undefined
  jest.mocked(tablesSelectTableSearchColumn).mockImplementation(
    () =>
      new Promise((_, reject) => {
        rejectWrite = reject
      }) as ReturnType<typeof tablesSelectTableSearchColumn>
  )
  setup(menus())
  const dialog = await chooseSearchItem(ENABLE)
  fireEvent.click(within(dialog).getByRole("button", { name: ENABLE }))
  const pending = await within(dialog).findByRole("button", {
    name: "Enabling...",
  })
  expect(pending).toBeDisabled()
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled()
  fireEvent.keyDown(dialog, { key: "Escape" })
  expect(screen.getByRole("alertdialog")).toBeInTheDocument()
  await act(async () => rejectWrite({ status: 503 }))
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Could not update semantic search",
        description: "Try again in a moment.",
      })
    )
  )
  expect(toast).toHaveBeenCalledTimes(1)
  fireEvent.click(await within(dialog).findByRole("button", { name: "Cancel" }))
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument()
  )
  openMenu()
  expect(
    await screen.findByRole("menuitem", { name: ENABLE })
  ).toBeInTheDocument()
})

test("long-row progress does not claim ready after backfill and retries only displayed failures", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "needs_attention",
    index: {
      state: "active",
      ready: 0,
      pending: 1,
      failed: 1,
      empty: 0,
      backfill_complete: true,
      partial: true,
    },
  }
  jest.mocked(tablesGetTableSearchProgress).mockResolvedValue({
    generation: 2,
    items: [
      {
        ...failedRow,
        expected_chunks: null,
        sampled_chunks: 100,
        sampled_embedded: 32,
        chunks_capped: true,
        error_code: "CREDENTIAL_INVALID",
      },
    ],
  })
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByLabelText("Semantic search: Needs attention")
  expect(badge).toHaveClass("border-amber-500")
  expect(badge).not.toHaveClass("bg-amber-100")
  expect(tablesGetTableSearchProgress).not.toHaveBeenCalled()
  openDetails()
  await screen.findByText(/The total is still being discovered/)
  expect(
    screen.getByText(/Check the AI provider credentials/)
  ).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: RETRY }))
  await waitFor(() =>
    expect(tablesRetryTableSearch).toHaveBeenCalledWith({
      workspaceId: "workspace-synthetic",
      tableId: "table-synthetic",
      requestBody: { expected_generation: 2, document_ids: ["doc-synthetic"] },
    })
  )
})

test.each(["provider", "index", "rebuild"] as const)(
  "failed row retries stay disabled during %s unavailability and recover after refresh",
  async (blockedSource) => {
    configuration = {
      ...configuration,
      selected_column_ids: [column.id],
      status: "needs_attention",
      index: {
        ...configuration.index,
        state: blockedSource === "index" ? "paused" : "active",
        failed: 1,
        pending: 0,
        backfill_complete: true,
      },
    }
    jest.mocked(searchGetEmbeddingConfiguration).mockResolvedValue({
      ...available,
      state: blockedSource === "provider" ? "paused" : "active",
      reindex_required: blockedSource === "rebuild",
    })
    jest
      .mocked(tablesGetTableSearchProgress)
      .mockResolvedValue({ generation: 2, items: [failedRow] })
    setup(provide(<TableSearchBadge />))
    let label = "Semantic search: Needs attention"
    if (blockedSource === "provider") label = "Semantic search: Unavailable"
    else if (blockedSource === "rebuild") label = "Semantic search: Indexing"
    await screen.findByLabelText(label)
    openDetails()
    const retry = await screen.findByRole("button", { name: RETRY })
    expect(retry).toBeDisabled()
    fireEvent.click(retry)
    expect(tablesRetryTableSearch).not.toHaveBeenCalled()

    configuration = {
      ...configuration,
      index: { ...configuration.index, state: "active" },
    }
    jest.mocked(searchGetEmbeddingConfiguration).mockResolvedValue(available)
    await act(async () => {
      invalidateArtifactQueries(client, "workspace-synthetic", {
        type: "table",
        id: "table-synthetic",
        title: "Synthetic table",
      })
      await client.invalidateQueries({
        queryKey: ["embedding-configuration", "workspace-synthetic"],
      })
    })
    await waitFor(() => expect(retry).toBeEnabled())
    fireEvent.click(retry)
    await waitFor(() => expect(tablesRetryTableSearch).toHaveBeenCalledTimes(1))
  }
)

test("provider failures show attention with one short line", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "ready",
  }
  setup(provide(<TableSearchBadge />))
  await screen.findByLabelText("Semantic search: Ready")
  openDetails()
  await screen.findByText("Model: openai / text-embedding-3-small")
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  jest
    .mocked(searchGetEmbeddingConfiguration)
    .mockRejectedValue({ status: 503 })
  await act(async () => {
    await client.invalidateQueries({
      queryKey: ["embedding-configuration", "workspace-synthetic"],
    })
  })
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load the search status."
    )
  )
  expect(
    screen.getAllByLabelText("Semantic search: Needs attention")
  ).toHaveLength(1)
  expect(toast).not.toHaveBeenCalled()
})

test("polling runs while chunks remain and stops at ready and on unmount", async () => {
  jest.useFakeTimers()
  try {
    configuration = {
      ...configuration,
      selected_column_ids: [column.id],
      status: "updating",
      index: {
        state: "active",
        ready: 0,
        pending: 1,
        failed: 0,
        empty: 0,
        backfill_complete: true,
        partial: true,
      },
    }
    const view = setup(provide(<TableSearchBadge />))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10)
    })
    expect(searchPollInterval(configuration)).toBe(3000)
    configuration = {
      ...configuration,
      status: "ready",
      index: {
        ...configuration.index,
        state: "active",
        pending: 0,
        backfill_complete: true,
      },
    }
    await act(async () => {
      await jest.advanceTimersByTimeAsync(3100)
    })
    expect(screen.getByLabelText("Semantic search: Ready")).toBeInTheDocument()
    const calls = jest.mocked(tablesGetTableSearch).mock.calls.length
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10000)
    })
    expect(tablesGetTableSearch).toHaveBeenCalledTimes(calls)
    view.unmount()
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10000)
    })
    expect(tablesGetTableSearch).toHaveBeenCalledTimes(calls)
  } finally {
    jest.useRealTimers()
  }
})

test("the header badge and the grid share one poll", async () => {
  jest.useFakeTimers()
  try {
    configuration = {
      ...configuration,
      selected_column_ids: [column.id],
      status: "updating",
      index: {
        ...configuration.index,
        state: "active",
        pending: 1,
        backfill_complete: true,
      },
    }
    const view = setup(provide(<TableSearchBadge />))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1500)
    })
    // The grid mounts later than the header, once the table has loaded.
    view.rerender(
      <QueryClientProvider client={client}>
        {provide(<TableSearchBadge />)}
        {menus()}
      </QueryClientProvider>
    )
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10)
    })
    const statusCalls = jest.mocked(tablesGetTableSearch).mock.calls.length
    const providerCalls = jest.mocked(searchGetEmbeddingConfiguration).mock
      .calls.length
    await act(async () => {
      await jest.advanceTimersByTimeAsync(9050)
    })
    expect(tablesGetTableSearch).toHaveBeenCalledTimes(statusCalls + 3)
    expect(searchGetEmbeddingConfiguration).toHaveBeenCalledTimes(
      providerCalls + 3
    )
  } finally {
    jest.useRealTimers()
  }
})

test("table artifact events refresh ready search status and counts", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "ready",
    index: {
      state: "active",
      ready: 1,
      pending: 0,
      failed: 0,
      empty: 0,
      backfill_complete: true,
      partial: false,
    },
  }
  setup(provide(<TableSearchBadge />))
  await screen.findByLabelText("Semantic search: Ready")
  expect(searchPollInterval(configuration, available)).toBe(false)
  const calls = jest.mocked(tablesGetTableSearch).mock.calls.length
  configuration = {
    ...configuration,
    status: "updating",
    index: { ...configuration.index, state: "active", ready: 0, pending: 1 },
  }
  await act(async () => {
    invalidateArtifactQueries(client, "workspace-synthetic", {
      type: "table",
      id: "other-table-synthetic",
      title: "Other table",
    })
  })
  expect(tablesGetTableSearch).toHaveBeenCalledTimes(calls)
  expect(screen.getByLabelText("Semantic search: Ready")).toBeInTheDocument()
  await act(async () => {
    invalidateArtifactQueries(client, "workspace-synthetic", {
      type: "table",
      id: "table-synthetic",
      title: "Synthetic table",
    })
  })
  await screen.findByLabelText("Semantic search: Indexing")
  openDetails()
  expect(await screen.findByText(/0 ready · 1 pending/)).toBeInTheDocument()
  expect(tablesGetTableSearch).toHaveBeenCalledTimes(calls + 1)
})

test("deleting a selected column explains the consequence", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  setup(menus())
  await waitFor(() =>
    expect(
      client.getQueryData([
        ...tableSearchKey("workspace-synthetic", "table-synthetic"),
        "configuration",
      ])
    ).toEqual(configuration)
  )
  openMenu()
  fireEvent.click(await screen.findByText("Delete column"))
  expect(
    await screen.findByText(/Deleting it removes it from search/)
  ).toBeInTheDocument()
})

test("a provider reindex marker overrides stale Ready", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "ready",
    index: {
      state: "active",
      ready: 1,
      pending: 0,
      failed: 0,
      empty: 0,
      backfill_complete: true,
      partial: false,
    },
  }
  jest
    .mocked(searchGetEmbeddingConfiguration)
    .mockResolvedValue({ ...available, reindex_required: true })
  setup(provide(<TableSearchBadge />))
  const badge = await screen.findByLabelText("Semantic search: Indexing")
  expect(badge.querySelector("svg")).toHaveClass("animate-spin")
})

test("provider failure allows removing selected columns but prevents new selections", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  jest
    .mocked(searchGetEmbeddingConfiguration)
    .mockRejectedValue({ status: 400 })
  jest.mocked(tablesSelectTableSearchColumn).mockResolvedValue(configuration)
  setup(menus([column, { ...column, id: "other-id", name: "other" }]))
  await waitFor(() =>
    expect(searchGetEmbeddingConfiguration).toHaveBeenCalled()
  )
  openMenu(1)
  const unselected = await screen.findByRole("menuitem", { name: ENABLE })
  await waitFor(() => expect(unselected).toHaveAttribute("data-disabled"))
  fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
  await waitFor(() =>
    expect(screen.queryByRole("menu")).not.toBeInTheDocument()
  )
  const dialog = await chooseSearchItem(DISABLE, 0)
  fireEvent.click(within(dialog).getByRole("button", { name: DISABLE }))
  await waitFor(() =>
    expect(tablesSelectTableSearchColumn).toHaveBeenCalledWith({
      workspaceId: "workspace-synthetic",
      tableId: "table-synthetic",
      requestBody: {
        column_id: column.id,
        enabled: false,
        expected_generation: 2,
      },
    })
  )
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Disabled vector search" })
    )
  )
})

test("configuration failure still prevents changing selected columns", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  setup(menus())
  openMenu()
  const item = await screen.findByRole("menuitem", { name: DISABLE })
  await waitFor(() => expect(item).not.toHaveAttribute("data-disabled"))
  jest.mocked(tablesGetTableSearch).mockRejectedValue({ status: 503 })
  await act(async () => {
    await client.invalidateQueries({
      queryKey: tableSearchKey("workspace-synthetic", "table-synthetic"),
    })
  })
  await waitFor(() => expect(item).toHaveAttribute("data-disabled"))
})

test.each([
  { pending: 1, backfill_complete: true },
  { pending: 0, backfill_complete: false },
])("failed rows do not stop polling unfinished work: %j", async (work) => {
  jest.useFakeTimers()
  try {
    configuration = {
      ...configuration,
      selected_column_ids: [column.id],
      status: "needs_attention",
      index: { ...configuration.index, state: "active", ...work, failed: 1 },
    }
    setup(provide(<TableSearchBadge />))
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10)
    })
    openDetails()
    await act(async () => {
      await jest.advanceTimersByTimeAsync(200)
    })
    const statusCalls = jest.mocked(tablesGetTableSearch).mock.calls.length
    const progressCalls = jest.mocked(tablesGetTableSearchProgress).mock.calls
      .length
    expect(progressCalls).toBeGreaterThan(0)
    await act(async () => {
      await jest.advanceTimersByTimeAsync(3100)
    })
    expect(jest.mocked(tablesGetTableSearch).mock.calls.length).toBeGreaterThan(
      statusCalls
    )
    expect(
      jest.mocked(tablesGetTableSearchProgress).mock.calls.length
    ).toBeGreaterThan(progressCalls)
    configuration = {
      ...configuration,
      index: {
        ...configuration.index,
        state: "active",
        pending: 0,
        backfill_complete: true,
      },
    }
    await act(async () => {
      await jest.advanceTimersByTimeAsync(3100)
    })
    const stoppedStatus = jest.mocked(tablesGetTableSearch).mock.calls.length
    const stoppedProgress = jest.mocked(tablesGetTableSearchProgress).mock.calls
      .length
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10000)
    })
    expect(tablesGetTableSearch).toHaveBeenCalledTimes(stoppedStatus)
    expect(tablesGetTableSearchProgress).toHaveBeenCalledTimes(stoppedProgress)
  } finally {
    jest.useRealTimers()
  }
})

test("polling stops when disabled, paused, or unavailable despite unfinished work", () => {
  const working: TableSearchConfiguration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "needs_attention",
    index: { ...configuration.index, state: "active", pending: 1, failed: 1 },
  }
  expect(searchPollInterval({ ...working, selected_column_ids: [] })).toBe(
    false
  )
  expect(searchPollInterval({ ...working, status: "disabled" })).toBe(false)
  expect(searchPollInterval({ ...working, status: "unavailable" })).toBe(false)
  expect(
    searchPollInterval({
      ...working,
      index: { ...working.index, state: "paused" },
    })
  ).toBe(false)
  expect(
    searchPollInterval({
      ...working,
      index: { ...working.index, state: "disabled" },
    })
  ).toBe(false)
  expect(searchPollInterval(working, { ...available, available: false })).toBe(
    false
  )
  expect(searchPollInterval(working, { ...available, state: "paused" })).toBe(
    false
  )
})

test("first selection keeps polling before the worker binds its configuration", () => {
  const initial: TableSearchConfiguration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "indexing",
    index: { state: "disabled", backfill_complete: false },
  }
  expect(searchPollInterval(initial, available)).toBe(3000)
  expect(
    searchPollInterval(
      { ...initial, status: "needs_attention" },
      {
        ...available,
        reindex_required: true,
      }
    )
  ).toBe(3000)
  expect(searchPollInterval({ ...initial, index: null }, available)).toBe(3000)
  expect(
    searchPollInterval(
      { ...initial, status: "needs_attention", index: null },
      available
    )
  ).toBe(false)
})
