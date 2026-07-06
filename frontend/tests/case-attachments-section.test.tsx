/**
 * @jest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { caseAttachmentsListAttachments } from "@/client"
import { CaseAttachmentsSection } from "@/components/cases/case-attachments-section"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    caseAttachmentsCreateAttachment: jest.fn(),
    caseAttachmentsDeleteAttachment: jest.fn(),
    caseAttachmentsDownloadAttachment: jest.fn(),
    caseAttachmentsListAttachments: jest.fn(),
  }
})

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  toast: jest.fn(),
}))

const mockCaseAttachmentsListAttachments =
  caseAttachmentsListAttachments as jest.MockedFunction<
    typeof caseAttachmentsListAttachments
  >
const mockToast = toast as jest.MockedFunction<typeof toast>
const mockUseWorkspaceDetails = useWorkspaceDetails as jest.MockedFunction<
  typeof useWorkspaceDetails
>

function renderCaseAttachmentsSection() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <CaseAttachmentsSection caseId="case-1" workspaceId="workspace-1" />
    </QueryClientProvider>
  )
}

describe("CaseAttachmentsSection", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockCaseAttachmentsListAttachments.mockResolvedValue([])
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: {
        settings: {
          effective_allowed_attachment_extensions: [] as string[],
          effective_allowed_attachment_mime_types: null,
        },
      },
      workspaceLoading: false,
      workspaceError: null,
    } as unknown as ReturnType<typeof useWorkspaceDetails>)
  })

  it("keeps disabled attachment upload control tabbable and blocks file picker on Enter", async () => {
    const inputClickSpy = jest.spyOn(HTMLInputElement.prototype, "click")

    renderCaseAttachmentsSection()

    const uploadControl = await screen.findByRole("button", {
      name: "Attachment uploads disabled",
    })

    expect(uploadControl).toHaveAttribute("aria-disabled", "true")
    expect(uploadControl).toHaveAttribute("tabindex", "0")

    fireEvent.keyDown(uploadControl, { key: "Enter" })

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({
        title: "Attachment uploads disabled",
        description: "Uploads are disabled for this workspace.",
      })
    })
    expect(inputClickSpy).not.toHaveBeenCalled()

    inputClickSpy.mockRestore()
  })
})
