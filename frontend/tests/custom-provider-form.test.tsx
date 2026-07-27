import { render, screen } from "@testing-library/react"
import { useForm } from "react-hook-form"
import type { AgentCustomProviderRead, CustomProviderType } from "@/client"
import { AdvancedSection } from "@/components/organization/custom-provider-fields"
import {
  buildProviderCreatePayload,
  buildProviderUpdatePayload,
  type CustomProviderFormValues,
  customProviderSchema,
  DEFAULT_CUSTOM_PROVIDER_VALUES,
  getCustomProviderIconId,
  getProviderDialogDefaults,
  typeDefaultPassthrough,
  typeSupportsCredentials,
  typeSupportsPassthrough,
} from "@/components/organization/custom-provider-form"
import { Form } from "@/components/ui/form"

const ALL_TYPES: CustomProviderType[] = [
  "generic_openai_compatible",
  "litellm",
  "ollama",
]

describe("custom provider type-aware helpers", () => {
  it("hides credentials only for ollama", () => {
    expect(typeSupportsCredentials("generic_openai_compatible")).toBe(true)
    expect(typeSupportsCredentials("litellm")).toBe(true)
    expect(typeSupportsCredentials("ollama")).toBe(false)
  })

  it("supports the passthrough toggle for every type", () => {
    for (const type of ALL_TYPES) {
      expect(typeSupportsPassthrough(type)).toBe(true)
    }
  })

  it("prefills litellm and ollama passthrough on, generic off", () => {
    expect(typeDefaultPassthrough("litellm")).toBe(true)
    expect(typeDefaultPassthrough("ollama")).toBe(true)
    expect(typeDefaultPassthrough("generic_openai_compatible")).toBe(false)
  })

  it("derives the icon from the stored type, not the name/url", () => {
    expect(getCustomProviderIconId("ollama")).toBe("ollama")
    // A provider named "ollama" but typed generic must NOT get the ollama icon.
    expect(getCustomProviderIconId("generic_openai_compatible")).toBe("custom")
    expect(getCustomProviderIconId("litellm")).toBe("litellm")
    expect(getCustomProviderIconId(null)).toBe("custom")
  })
})

describe("buildProviderCreatePayload", () => {
  it("drops credentials for ollama but sends passthrough verbatim", () => {
    const payload = buildProviderCreatePayload({
      type: "ollama",
      displayName: "Local Ollama",
      baseUrl: "http://localhost:11434",
      apiKeyHeader: "Authorization",
      apiKey: "should-be-dropped",
      customHeadersJson: "",
      passthrough: true,
    })

    expect(payload.type).toBe("ollama")
    expect(payload.api_key).toBeNull()
    expect(payload.api_key_header).toBeNull()
    expect(payload.passthrough).toBe(true)
  })

  it("sends the passthrough form value verbatim for every type", () => {
    for (const type of ALL_TYPES) {
      for (const passthrough of [true, false]) {
        const payload = buildProviderCreatePayload({
          ...DEFAULT_CUSTOM_PROVIDER_VALUES,
          type,
          displayName: "x",
          passthrough,
        })
        expect(payload.passthrough).toBe(passthrough)
      }
    }
  })
})

describe("buildProviderUpdatePayload", () => {
  it("sends the passthrough form value verbatim for every type", () => {
    for (const type of ALL_TYPES) {
      for (const passthrough of [true, false]) {
        const payload = buildProviderUpdatePayload({
          ...DEFAULT_CUSTOM_PROVIDER_VALUES,
          type,
          displayName: "x",
          passthrough,
        })
        expect(payload.passthrough).toBe(passthrough)
      }
    }
  })
})

describe("customProviderSchema", () => {
  it("accepts every type/passthrough combination", () => {
    for (const type of ALL_TYPES) {
      for (const passthrough of [true, false]) {
        const result = customProviderSchema.safeParse({
          ...DEFAULT_CUSTOM_PROVIDER_VALUES,
          type,
          displayName: "x",
          passthrough,
        })
        expect(result.success).toBe(true)
      }
    }
  })
})

function AdvancedHarness({
  type,
  surface = "edit",
}: {
  type: CustomProviderType
  surface?: "wizard" | "edit"
}) {
  const form = useForm<CustomProviderFormValues>({
    defaultValues: { ...DEFAULT_CUSTOM_PROVIDER_VALUES, type },
  })
  return (
    <Form {...form}>
      <AdvancedSection
        form={form}
        type={type}
        open={true}
        onOpenChange={() => {}}
        surface={surface}
      />
    </Form>
  )
}

describe("AdvancedSection", () => {
  it("renders the passthrough toggle inside Advanced for every type on the edit surface", () => {
    for (const type of ALL_TYPES) {
      const { unmount } = render(<AdvancedHarness type={type} surface="edit" />)
      expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
      unmount()
    }
  })

  it("hides the passthrough toggle on the wizard surface for litellm/ollama only", () => {
    const hidden: CustomProviderType[] = ["litellm", "ollama"]
    for (const type of hidden) {
      const { unmount } = render(
        <AdvancedHarness type={type} surface="wizard" />
      )
      expect(screen.queryByText("Passthrough mode")).not.toBeInTheDocument()
      expect(screen.getByLabelText("Additional headers")).toBeInTheDocument()
      unmount()
    }

    // Generic keeps the visible toggle in the wizard.
    render(
      <AdvancedHarness type="generic_openai_compatible" surface="wizard" />
    )
    expect(screen.getByText("Passthrough mode")).toBeInTheDocument()
  })

  it("drops the API-key mention from the headers copy for ollama", () => {
    render(<AdvancedHarness type="ollama" />)
    expect(screen.getByText(/extra non-auth headers/i)).toBeInTheDocument()
    expect(
      screen.queryByText(/not covered by the API key above/i)
    ).not.toBeInTheDocument()
  })

  it("keeps the API-key mention in the headers copy for non-ollama types", () => {
    render(<AdvancedHarness type="generic_openai_compatible" />)
    expect(
      screen.getByText(/not covered by the API key above/i)
    ).toBeInTheDocument()
  })
})

describe("getProviderDialogDefaults", () => {
  it("carries the stored type and passthrough into edit defaults", () => {
    const provider: AgentCustomProviderRead = {
      id: "p1",
      organization_id: "org-1",
      display_name: "My Ollama",
      base_url: "http://localhost:11434",
      type: "ollama",
      passthrough: true,
      api_key_header: null,
      last_refreshed_at: null,
    }
    const defaults = getProviderDialogDefaults(provider)
    expect(defaults.type).toBe("ollama")
    // Edit dialog shows the stored value with no type-driven mutation.
    expect(defaults.passthrough).toBe(true)
  })

  it("defaults to generic for a new provider", () => {
    expect(getProviderDialogDefaults(null).type).toBe(
      "generic_openai_compatible"
    )
  })
})
