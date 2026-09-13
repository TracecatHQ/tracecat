import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { PlatformAuditSettings } from "@/components/admin/platform-audit-settings"
import { OrgSettingsAuditForm } from "@/components/organization/org-settings-audit"
import { useAdminAuditSettings } from "@/hooks/use-admin"
import { useOrgAuditSettings } from "@/lib/hooks"

jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="Custom payload editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))

jest.mock("@/lib/hooks", () => ({
  useOrgAuditSettings: jest.fn(),
}))

jest.mock("@/hooks/use-admin", () => ({
  useAdminAuditSettings: jest.fn(),
}))

const testAuditWebhook = jest.fn()

const auditSettings = {
  audit_webhook_url: "https://audit.example.com/first",
  audit_webhook_custom_headers: {
    Authorization: "Bearer retained-secret",
    "X-Audit-Tenant": "retained-tenant",
  },
  audit_webhook_custom_payload: null,
  audit_webhook_payload_attribute: null,
  audit_webhook_verify_ssl: true,
  decryption_failed_keys: [],
}

const updateOrgAuditSettings = jest.fn(async () => undefined)
const updatePlatformAuditSettings = jest.fn(async () => auditSettings)

function mockAuditHooks(settings = auditSettings) {
  const value = {
    auditSettings: settings,
    auditSettingsIsLoading: false,
    auditSettingsError: null,
    updateAuditSettingsIsPending: false,
    updateAuditSettingsError: null,
    testAuditWebhook,
    testAuditWebhookIsPending: false,
  }
  jest.mocked(useOrgAuditSettings).mockReturnValue({
    ...value,
    updateAuditSettings: updateOrgAuditSettings,
  } as ReturnType<typeof useOrgAuditSettings>)
  jest.mocked(useAdminAuditSettings).mockReturnValue({
    ...value,
    updateAuditSettings: updatePlatformAuditSettings,
  } as ReturnType<typeof useAdminAuditSettings>)
}

const surfaces = [
  ["organization", OrgSettingsAuditForm, updateOrgAuditSettings],
  ["platform", PlatformAuditSettings, updatePlatformAuditSettings],
] as const

describe.each(surfaces)(
  "%s audit webhook settings",
  (_name, SettingsForm, updateAuditSettings) => {
    beforeEach(() => {
      updateAuditSettings.mockClear()
      testAuditWebhook.mockClear()
      mockAuditHooks()
    })

    it("clears retained headers when the webhook origin changes", async () => {
      const user = userEvent.setup()
      render(<SettingsForm />)
      await user.click(screen.getByRole("button", { name: "Update" }))

      const url = screen.getByLabelText("Audit webhook URL")
      await user.clear(url)
      await user.type(url, "https://other.example.com/second")
      await user.tab()

      expect(
        screen.getByText(
          /Saved headers were cleared because the webhook origin/
        )
      ).toBeInTheDocument()
      expect(screen.queryByDisplayValue("Bearer retained-secret")).toBeNull()
      await user.click(screen.getByRole("button", { name: "Save changes" }))

      await waitFor(() => expect(updateAuditSettings).toHaveBeenCalledTimes(1))
      expect(updateAuditSettings).toHaveBeenCalledWith({
        requestBody: expect.objectContaining({
          audit_webhook_url: "https://other.example.com/second",
          audit_webhook_custom_headers: null,
        }),
      })
    })

    it("preserves retained headers for a same-origin path change", async () => {
      const user = userEvent.setup()
      render(<SettingsForm />)
      await user.click(screen.getByRole("button", { name: "Update" }))

      const url = screen.getByLabelText("Audit webhook URL")
      await user.clear(url)
      await user.type(url, "https://AUDIT.example.com:443/second?version=2")
      await user.tab()
      await user.click(screen.getByRole("button", { name: "Save changes" }))

      await waitFor(() => expect(updateAuditSettings).toHaveBeenCalledTimes(1))
      expect(updateAuditSettings).toHaveBeenCalledWith({
        requestBody: expect.objectContaining({
          audit_webhook_custom_headers:
            auditSettings.audit_webhook_custom_headers,
        }),
      })
    })

    it.each([
      [
        "adds a DNS root dot",
        "https://audit.example.com/first",
        "https://AUDIT.example.com.:443/second",
      ],
      [
        "removes a DNS root dot",
        "https://audit.example.com./first",
        "https://AUDIT.example.com:443/second",
      ],
    ])(
      "preserves retained headers when a same-origin edit %s",
      async (_scenario, savedUrl, nextUrl) => {
        mockAuditHooks({
          ...auditSettings,
          audit_webhook_url: savedUrl,
        })
        const user = userEvent.setup()
        render(<SettingsForm />)
        await user.click(screen.getByRole("button", { name: "Update" }))

        const url = screen.getByLabelText("Audit webhook URL")
        await user.clear(url)
        await user.type(url, nextUrl)
        await user.tab()
        expect(
          screen.queryByText(
            /Saved headers were cleared because the webhook origin/
          )
        ).toBeNull()
        await user.click(screen.getByRole("button", { name: "Save changes" }))

        await waitFor(() =>
          expect(updateAuditSettings).toHaveBeenCalledTimes(1)
        )
        expect(updateAuditSettings).toHaveBeenCalledWith({
          requestBody: expect.objectContaining({
            audit_webhook_custom_headers:
              auditSettings.audit_webhook_custom_headers,
          }),
        })
      }
    )

    it("accepts replacement headers after an origin change", async () => {
      const user = userEvent.setup()
      render(<SettingsForm />)
      await user.click(screen.getByRole("button", { name: "Update" }))

      const url = screen.getByLabelText("Audit webhook URL")
      await user.clear(url)
      await user.type(url, "https://other.example.com/second")
      await user.tab()
      await user.click(screen.getByRole("button", { name: "Add header" }))
      await user.type(
        screen.getByPlaceholderText("X-Custom-Header"),
        "Authorization"
      )
      await user.type(
        screen.getByPlaceholderText("Header value"),
        "Bearer new-secret"
      )
      await user.click(screen.getByRole("button", { name: "Save changes" }))

      await waitFor(() => expect(updateAuditSettings).toHaveBeenCalledTimes(1))
      expect(updateAuditSettings).toHaveBeenCalledWith({
        requestBody: expect.objectContaining({
          audit_webhook_custom_headers: {
            Authorization: "Bearer new-secret",
          },
        }),
      })
    })
  }
)
