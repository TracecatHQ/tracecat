import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
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
import { TableSearchColumnControl } from "@/components/tables/table-search-column-control"
import { TableSearchProvider } from "@/components/tables/table-search-context"
import { TableSearchStatus } from "@/components/tables/table-search-status"
import { TableViewColumnMenu } from "@/components/tables/table-view-column-menu"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
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
  useDeleteColumn: () => ({ deleteColumn: jest.fn() }),
  useUpdateColumn: () => ({ updateColumn: jest.fn() }),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

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
let configuration: TableSearchConfiguration
let permissions: Set<string>
let client: QueryClient

function setup(children: ReactNode) {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <TableSearchProvider tableId="table-synthetic">
        {children}
      </TableSearchProvider>
    </QueryClientProvider>
  )
}
function controls(columns = [column]) {
  return (
    <DropdownMenu open modal={false}>
      <DropdownMenuTrigger>Columns</DropdownMenuTrigger>
      <DropdownMenuContent forceMount>
        {columns.map((item) => (
          <TableSearchColumnControl key={item.id} column={item} />
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  permissions = new Set([
    "workspace:read",
    "table:read",
    "table:update",
    "table:delete",
    "org:settings:read",
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

test("read permissions gate all authenticated status requests", () => {
  permissions.delete("workspace:read")
  setup(
    <>
      <TableSearchStatus />
      {controls()}
    </>
  )
  expect(tablesGetTableSearch).not.toHaveBeenCalled()
  expect(searchGetEmbeddingConfiguration).not.toHaveBeenCalled()
  expect(screen.queryByText(/Semantic search:/)).not.toBeInTheDocument()
})

test("read-only users see destination but cannot change selection or provider settings", async () => {
  permissions.delete("table:update")
  permissions.delete("org:settings:read")
  setup(controls())
  await screen.findByText(/Selected text and queries are sent to/)
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute("data-disabled")
  expect(screen.queryByText("AI provider settings")).not.toBeInTheDocument()
})

test("missing provider disables semantic selection and keeps existing table controls", async () => {
  jest.mocked(searchGetEmbeddingConfiguration).mockResolvedValue({
    available: false,
    version: 0,
    state: "disabled",
    configuration: null,
  })
  setup(
    <>
      <TableSearchStatus />
      <TableViewColumnMenu column={column} />
    </>
  )
  await screen.findByText("Semantic search: Unavailable")
  fireEvent.keyDown(screen.getByRole("button", { name: "Configure column" }), {
    key: "ArrowDown",
  })
  await screen.findByText("Remove unique index")
  expect(screen.getByText("Delete column")).toBeInTheDocument()
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute("data-disabled")
})

test("non-text columns cannot be enabled", async () => {
  setup(controls([{ ...column, type: "INTEGER" }]))
  await screen.findByText(/Selected text and queries/)
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute("data-disabled")
  expect(screen.getByText(/Only TEXT columns/)).toBeInTheDocument()
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
    controls([
      column,
      { ...column, id: "summary-id", name: "summary", is_index: false },
    ])
  )
  await waitFor(() =>
    expect(screen.getAllByRole("menuitemcheckbox")[0]).not.toHaveAttribute(
      "data-disabled"
    )
  )
  fireEvent.click(screen.getAllByRole("menuitemcheckbox")[0])
  await waitFor(() =>
    expect(screen.getAllByRole("menuitemcheckbox")[0]).toHaveAttribute(
      "aria-checked",
      "true"
    )
  )
  await waitFor(() =>
    expect(screen.getAllByRole("menuitemcheckbox")[1]).not.toHaveAttribute(
      "data-disabled"
    )
  )
  fireEvent.click(screen.getAllByRole("menuitemcheckbox")[1])
  await waitFor(() =>
    expect(screen.getAllByRole("menuitemcheckbox")[1]).toHaveAttribute(
      "aria-checked",
      "true"
    )
  )
  expect(tablesSelectTableSearchColumn).toHaveBeenNthCalledWith(2, {
    workspaceId: "workspace-synthetic",
    tableId: "table-synthetic",
    requestBody: {
      column_id: "summary-id",
      enabled: true,
      expected_generation: 3,
    },
  })
  expect(column.is_index).toBe(true)
})

test("conflicts refresh and preserve confirmed selection without a success toast", async () => {
  jest.mocked(tablesSelectTableSearchColumn).mockRejectedValue({ status: 409 })
  setup(controls())
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).not.toHaveAttribute(
      "data-disabled"
    )
  )
  fireEvent.click(screen.getByRole("menuitemcheckbox"))
  await waitFor(() =>
    expect(toast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Search settings changed" })
    )
  )
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute(
    "aria-checked",
    "false"
  )
  expect(tablesGetTableSearch).toHaveBeenCalledTimes(2)
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
        document_id: "doc-synthetic",
        row_id: "row-synthetic",
        state: "failed",
        revision: 1,
        expected_chunks: null,
        sampled_chunks: 100,
        sampled_embedded: 32,
        chunks_capped: true,
        error_code: "CREDENTIAL_INVALID",
      },
    ],
  })
  setup(<TableSearchStatus />)
  await screen.findByText("Semantic search: Needs attention")
  expect(tablesGetTableSearchProgress).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole("button", { name: "Semantic search: Needs attention" })
  )
  await screen.findByText(/The total is still being discovered/)
  expect(
    screen.getByText(/Check the AI provider credentials/)
  ).toBeInTheDocument()
  fireEvent.click(
    screen.getByRole("button", { name: "Retry failed rows on this page" })
  )
  await waitFor(() =>
    expect(tablesRetryTableSearch).toHaveBeenCalledWith({
      workspaceId: "workspace-synthetic",
      tableId: "table-synthetic",
      requestBody: { expected_generation: 2, document_ids: ["doc-synthetic"] },
    })
  )
})

test("provider failures show attention and credential-only rotation does not announce rebuild", async () => {
  configuration = {
    ...configuration,
    selected_column_ids: [column.id],
    status: "ready",
  }
  setup(<TableSearchStatus />)
  await screen.findByText("Semantic search: Ready")
  fireEvent.click(
    screen.getByRole("button", { name: "Semantic search: Ready" })
  )
  expect(screen.queryByText(/settings changed/)).not.toBeInTheDocument()
  jest
    .mocked(searchGetEmbeddingConfiguration)
    .mockRejectedValue({ status: 503 })
  fireEvent.click(screen.getByRole("button", { name: "Refresh search status" }))
  await screen.findByText("Semantic search: Needs attention")
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Could not load semantic search status"
  )
})

test("polling runs while chunks remain and stops at ready and on unmount", async () => {
  jest.useFakeTimers()
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
  const view = setup(<TableSearchStatus />)
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
  expect(screen.getByText("Semantic search: Ready")).toBeInTheDocument()
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
  jest.useRealTimers()
})

test("deleting a selected column explains the consequence", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  setup(<TableViewColumnMenu column={column} />)
  await waitFor(() =>
    expect(
      client.getQueryData([
        ...tableSearchKey("workspace-synthetic", "table-synthetic"),
        "configuration",
      ])
    ).toEqual(configuration)
  )
  fireEvent.keyDown(screen.getByRole("button", { name: "Configure column" }), {
    key: "ArrowDown",
  })
  fireEvent.click(await screen.findByText("Delete column"))
  expect(
    await screen.findByText(/Deleting it removes it from search/)
  ).toBeInTheDocument()
})

test("selection stays disabled while its write is pending and a failure keeps confirmed state", async () => {
  let rejectWrite: (error: unknown) => void = () => undefined
  jest.mocked(tablesSelectTableSearchColumn).mockImplementation(
    () =>
      new Promise((_, reject) => {
        rejectWrite = reject
      }) as ReturnType<typeof tablesSelectTableSearchColumn>
  )
  setup(controls())
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).not.toHaveAttribute(
      "data-disabled"
    )
  )
  fireEvent.click(screen.getByRole("menuitemcheckbox"))
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute(
      "data-disabled"
    )
  )
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute(
    "aria-checked",
    "false"
  )
  await act(async () => rejectWrite({ status: 503 }))
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).not.toHaveAttribute(
      "data-disabled"
    )
  )
  expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute(
    "aria-checked",
    "false"
  )
})

test("a provider reindex marker overrides stale Ready and explains rebuilding", async () => {
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
  setup(<TableSearchStatus />)
  await screen.findByText("Semantic search: Updating")
  fireEvent.click(
    screen.getByRole("button", { name: "Semantic search: Updating" })
  )
  expect(screen.getByText(/settings changed/)).toBeInTheDocument()
})

test("provider failure allows removing selected columns but prevents new selections", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  jest
    .mocked(searchGetEmbeddingConfiguration)
    .mockRejectedValue({ status: 400 })
  jest.mocked(tablesSelectTableSearchColumn).mockResolvedValue(configuration)
  setup(controls([column, { ...column, id: "other-id", name: "other" }]))
  await screen.findAllByText(/Provider or index status could not be loaded/)
  const [selected, unselected] = screen.getAllByRole("menuitemcheckbox")
  await waitFor(() => expect(selected).not.toHaveAttribute("data-disabled"))
  expect(unselected).toHaveAttribute("data-disabled")
  fireEvent.click(selected)
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
})

test("configuration failure still prevents changing selected columns", async () => {
  configuration = { ...configuration, selected_column_ids: [column.id] }
  setup(controls())
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).not.toHaveAttribute(
      "data-disabled"
    )
  )
  jest.mocked(tablesGetTableSearch).mockRejectedValue({ status: 503 })
  await act(async () => {
    await client.invalidateQueries({
      queryKey: tableSearchKey("workspace-synthetic", "table-synthetic"),
    })
  })
  await waitFor(() =>
    expect(screen.getByRole("menuitemcheckbox")).toHaveAttribute(
      "data-disabled"
    )
  )
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
    setup(<TableSearchStatus />)
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10)
    })
    fireEvent.click(
      screen.getByRole("button", { name: "Semantic search: Needs attention" })
    )
    await act(async () => {
      await jest.advanceTimersByTimeAsync(10)
    })
    const statusCalls = jest.mocked(tablesGetTableSearch).mock.calls.length
    const progressCalls = jest.mocked(tablesGetTableSearchProgress).mock.calls
      .length
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
