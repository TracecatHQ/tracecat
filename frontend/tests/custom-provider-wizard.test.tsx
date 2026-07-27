import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  createCustomProvider,
  listCatalog,
  refreshCustomProviderCatalog,
  updateCustomProvider,
  validateCustomProviderConnection,
} from "@/client"
import { CustomProviderDialog } from "@/components/organization/custom-provider-dialog"
import { CustomProviderWizard } from "@/components/organization/custom-provider-wizard"

jest.mock("@/client", () => ({
  validateCustomProviderConnection: jest.fn(),
  createCustomProvider: jest.fn(),
  refreshCustomProviderCatalog: jest.fn(),
  updateCustomProvider: jest.fn(),
  deleteCustomProvider: jest.fn(),
  listCatalog: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

jest.mock("@/components/icons", () => ({
  ProviderIcon: ({ providerId }: { providerId: string }) => (
    <span data-testid={`icon-${providerId}`} />
  ),
}))

// Render dialog content unconditionally so the form is queryable.
jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

const mockValidate = jest.mocked(validateCustomProviderConnection)
const mockCreate = jest.mocked(createCustomProvider)
const mockRefresh = jest.mocked(refreshCustomProviderCatalog)
const mockUpdate = jest.mocked(updateCustomProvider)
const mockListCatalog = jest.mocked(listCatalog)

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("CustomProviderWizard", () => {
  it("gates Finish behind a successful connection test", async () => {
    const user = userEvent.setup()
    mockValidate.mockResolvedValue({ valid: true })
    mockCreate.mockResolvedValue({
      id: "prov-1",
      organization_id: "org-1",
      display_name: "Gateway",
      base_url: "https://gw.example.com/v1",
      type: "generic_openai_compatible",
      passthrough: false,
      api_key_header: null,
      last_refreshed_at: null,
    })
    mockRefresh.mockResolvedValue(undefined)
    mockListCatalog.mockResolvedValue({
      items: [
        {
          id: "cat-1",
          custom_provider_id: "prov-1",
          organization_id: "org-1",
          model_provider: "custom",
          model_name: "gw-model-a",
          model_metadata: {},
        },
      ],
      next_cursor: null,
    })

    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    // Step 1: continue with the default (generic) type.
    await user.click(screen.getByRole("button", { name: "Continue" }))

    // Step 2: fill name + base URL, then continue to the test step.
    await user.type(screen.getByLabelText("Name"), "Gateway")
    await user.type(
      screen.getByLabelText("Base URL"),
      "https://gw.example.com/v1"
    )
    await user.click(screen.getByRole("button", { name: "Continue" }))

    // Step 3: Finish must be disabled until the connection is verified.
    const finishButton = screen.getByRole("button", { name: "Finish" })
    expect(finishButton).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Test connection" }))

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Finish" })).toBeEnabled()
    })
    expect(mockValidate).toHaveBeenCalled()
    expect(mockCreate).toHaveBeenCalled()
    expect(mockRefresh).toHaveBeenCalledWith({ providerId: "prov-1" })
    expect(screen.getByText("gw-model-a")).toBeInTheDocument()
  })

  it("invalidates the model-access query after a successful creation", async () => {
    const user = userEvent.setup()
    mockValidate.mockResolvedValue({ valid: true })
    mockCreate.mockResolvedValue({
      id: "prov-1",
      organization_id: "org-1",
      display_name: "Gateway",
      base_url: "https://gw.example.com/v1",
      type: "generic_openai_compatible",
      passthrough: false,
      api_key_header: null,
      last_refreshed_at: null,
    })
    mockRefresh.mockResolvedValue(undefined)
    mockListCatalog.mockResolvedValue({
      items: [
        {
          id: "cat-1",
          custom_provider_id: "prov-1",
          organization_id: "org-1",
          model_provider: "custom",
          model_name: "gw-model-a",
          model_metadata: {},
        },
      ],
      next_cursor: null,
    })

    const queryClient = new QueryClient({
      defaultOptions: {
        mutations: { retry: false },
        queries: { retry: false },
      },
    })
    const invalidateSpy = jest.spyOn(queryClient, "invalidateQueries")

    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    })

    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.type(screen.getByLabelText("Name"), "Gateway")
    await user.type(
      screen.getByLabelText("Base URL"),
      "https://gw.example.com/v1"
    )
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Test connection" }))

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Finish" })).toBeEnabled()
    })

    // Discovery auto-enables the discovered models, so the settings view must
    // refetch access rows to avoid rendering them as disabled.
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["organization", "agent-model-access"],
    })
  })

  it("exposes the passthrough toggle inside Advanced for a generic provider", async () => {
    const user = userEvent.setup()
    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    // Step 1: continue with the default (generic) type.
    await user.click(screen.getByRole("button", { name: "Continue" }))

    // The passthrough toggle lives inside the collapsed Advanced section, so it
    // is not visible until the section is opened.
    expect(screen.queryByText("Passthrough mode")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Advanced" }))

    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("hides credentials and the passthrough toggle for ollama, even with Advanced expanded", async () => {
    const user = userEvent.setup()
    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    // Pick the Ollama type card, then continue to the config step.
    await user.click(screen.getByRole("button", { name: /Ollama/ }))
    await user.click(screen.getByRole("button", { name: "Continue" }))

    expect(screen.getByLabelText("Base URL")).toBeInTheDocument()
    expect(screen.queryByLabelText("Auth value")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Auth header")).not.toBeInTheDocument()

    // The wizard hides passthrough for ollama; it stays silently prefilled on.
    await user.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.queryByText("Passthrough mode")).not.toBeInTheDocument()
    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("hides the passthrough toggle for litellm, even with Advanced expanded", async () => {
    const user = userEvent.setup()
    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    await user.click(screen.getByRole("button", { name: /LiteLLM/ }))
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Advanced" }))

    expect(screen.queryByText("Passthrough mode")).not.toBeInTheDocument()
    expect(screen.queryByRole("switch")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("still creates litellm with passthrough=true despite the hidden toggle", async () => {
    const user = userEvent.setup()
    mockValidate.mockResolvedValue({ valid: true })
    mockCreate.mockResolvedValue({
      id: "prov-litellm",
      organization_id: "org-1",
      display_name: "Proxy",
      base_url: "http://localhost:4000",
      type: "litellm",
      passthrough: true,
      api_key_header: null,
      last_refreshed_at: null,
    })
    mockRefresh.mockResolvedValue(undefined)
    mockListCatalog.mockResolvedValue({ items: [], next_cursor: null })

    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    await user.click(screen.getByRole("button", { name: /LiteLLM/ }))
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.type(screen.getByLabelText("Name"), "Proxy")
    await user.type(screen.getByLabelText("Base URL"), "http://localhost:4000")
    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Test connection" }))

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalled()
    })
    const requestBody = mockCreate.mock.calls[0]?.[0]?.requestBody
    expect(requestBody?.type).toBe("litellm")
    expect(requestBody?.passthrough).toBe(true)
  })

  it("prefills the passthrough toggle off for a generic provider", async () => {
    const user = userEvent.setup()
    render(<CustomProviderWizard open={true} onOpenChange={jest.fn()} />, {
      wrapper,
    })

    await user.click(screen.getByRole("button", { name: "Continue" }))
    await user.click(screen.getByRole("button", { name: "Advanced" }))

    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
    expect(screen.getByRole("switch")).not.toBeChecked()
  })
})

describe("CustomProviderDialog (edit)", () => {
  const ollamaProvider = {
    id: "prov-ollama",
    organization_id: "org-1",
    display_name: "Local Ollama",
    base_url: "http://localhost:11434",
    type: "ollama" as const,
    passthrough: false,
    api_key_header: null,
    last_refreshed_at: null,
  }

  const genericProvider = {
    id: "prov-generic",
    organization_id: "org-1",
    display_name: "Gateway",
    base_url: "https://gw.example.com/v1",
    type: "generic_openai_compatible" as const,
    passthrough: false,
    api_key_header: null,
    last_refreshed_at: null,
  }

  const litellmProvider = {
    id: "prov-litellm",
    organization_id: "org-1",
    display_name: "Proxy",
    base_url: "http://localhost:4000",
    type: "litellm" as const,
    passthrough: true,
    api_key_header: null,
    last_refreshed_at: null,
  }

  it("exposes the passthrough toggle inside Advanced for a litellm provider", async () => {
    const user = userEvent.setup()
    render(
      <CustomProviderDialog
        provider={litellmProvider}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    // Unlike the wizard, the edit dialog always shows the toggle for litellm,
    // reflecting the stored value (true here).
    await user.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
    expect(screen.getByRole("switch")).toBeChecked()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("hides credentials but exposes the passthrough toggle for an ollama provider", async () => {
    const user = userEvent.setup()
    render(
      <CustomProviderDialog
        provider={ollamaProvider}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    expect(screen.queryByLabelText("Auth value")).not.toBeInTheDocument()

    // The passthrough toggle is present for Ollama and reflects the stored
    // value (false here) with no type-driven mutation.
    await user.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
    expect(screen.getByRole("switch")).not.toBeChecked()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("exposes the passthrough toggle inside Advanced for a generic provider", async () => {
    const user = userEvent.setup()
    render(
      <CustomProviderDialog
        provider={genericProvider}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    // Collapsed by default, then revealed inside the Advanced section.
    expect(screen.queryByText("Passthrough mode")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Advanced" }))
    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
    expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
  })

  it("exposes an editable type selector on the edit path", () => {
    render(
      <CustomProviderDialog
        provider={ollamaProvider}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    // The type control is a combobox (select) rather than a fixed label, so the
    // type is editable when editing an existing provider.
    const typeSelect = screen.getByRole("combobox")
    expect(typeSelect).toBeInTheDocument()
    expect(typeSelect).toHaveTextContent("Ollama")
  })

  it("submits the stored type unchanged when saving without edits", async () => {
    const user = userEvent.setup()
    mockUpdate.mockResolvedValue({
      ...ollamaProvider,
    })

    render(
      <CustomProviderDialog
        provider={ollamaProvider}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    await user.click(screen.getByRole("button", { name: "Save source" }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    const requestBody = mockUpdate.mock.calls[0]?.[0]?.requestBody
    expect(requestBody?.type).toBe("ollama")
    // Saving unchanged submits the stored passthrough value verbatim.
    expect(requestBody?.passthrough).toBe(false)
  })

  it("submits the stored ollama passthrough=true unchanged on save", async () => {
    const user = userEvent.setup()
    const passthroughOllama = { ...ollamaProvider, passthrough: true }
    mockUpdate.mockResolvedValue({ ...passthroughOllama })

    render(
      <CustomProviderDialog
        provider={passthroughOllama}
        open={true}
        onOpenChange={jest.fn()}
      />,
      { wrapper }
    )

    await user.click(screen.getByRole("button", { name: "Save source" }))

    await waitFor(() => {
      expect(mockUpdate).toHaveBeenCalled()
    })
    const requestBody = mockUpdate.mock.calls[0]?.[0]?.requestBody
    expect(requestBody?.type).toBe("ollama")
    expect(requestBody?.passthrough).toBe(true)
  })
})
