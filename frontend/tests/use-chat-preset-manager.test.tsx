import { renderHook } from "@testing-library/react"
import type {
  AgentPresetRead,
  AgentPresetVersionRead,
  AgentSessionsGetSessionVercelResponse,
} from "@/client"
import { useChatPresetManager } from "@/hooks/use-chat-preset-manager"
import type { TracecatApiError } from "@/lib/errors"

const mockUseAgentPreset = jest.fn()
const mockUseAgentPresets = jest.fn()
const mockUseAgentPresetVersion = jest.fn()
const mockUseAgentPresetVersions = jest.fn()

jest.mock("@/hooks/use-agent-presets", () => ({
  useAgentPreset: (...args: unknown[]) => mockUseAgentPreset(...args),
  useAgentPresets: (...args: unknown[]) => mockUseAgentPresets(...args),
  useAgentPresetVersion: (...args: unknown[]) =>
    mockUseAgentPresetVersion(...args),
  useAgentPresetVersions: (...args: unknown[]) =>
    mockUseAgentPresetVersions(...args),
}))

describe("useChatPresetManager", () => {
  const preset = {
    id: "preset-1",
    name: "Preset",
    model_name: "gpt-5",
    model_provider: "openai",
    base_url: null,
    current_version_id: "version-2",
  } as AgentPresetRead

  beforeEach(() => {
    mockUseAgentPresets.mockReturnValue({
      presets: [],
      presetsIsLoading: false,
      presetsError: null,
    })
    mockUseAgentPreset.mockReturnValue({
      preset,
      presetIsLoading: false,
    })
    mockUseAgentPresetVersions.mockReturnValue({
      versions: [],
      versionsIsLoading: false,
      versionsError: null,
    })
    mockUseAgentPresetVersion.mockReturnValue({
      presetVersion: null,
      presetVersionIsLoading: false,
      presetVersionError: null,
    })
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  it("does not fall back to the head preset when a pinned version fails to load", () => {
    const pinnedVersionError = new Error(
      "Pinned preset version lookup failed"
    ) as TracecatApiError
    mockUseAgentPresetVersion.mockReturnValue({
      presetVersion: null,
      presetVersionIsLoading: false,
      presetVersionError: pinnedVersionError,
    })

    const chat = {
      agent_preset_id: "preset-1",
      agent_preset_version_id: "version-1",
    } as AgentSessionsGetSessionVercelResponse

    const { result } = renderHook(() =>
      useChatPresetManager({
        workspaceId: "workspace-1",
        chat,
        updateChat: jest.fn(),
        isUpdatingChat: false,
        chatLoading: false,
        selectedChatId: "chat-1",
      })
    )

    expect(result.current.selectedPresetVersionId).toBe("version-1")
    expect(result.current.selectedPresetConfig).toBeNull()
    expect(result.current.selectedPresetConfigError).toBe(pinnedVersionError)
  })

  it("uses the pinned version config when it loads successfully", () => {
    const presetVersion = {
      id: "version-1",
      version: 1,
      model_name: "gpt-5-mini",
      model_provider: "openai",
      base_url: "https://example.com",
    } as AgentPresetVersionRead
    mockUseAgentPresetVersion.mockReturnValue({
      presetVersion,
      presetVersionIsLoading: false,
      presetVersionError: null,
    })

    const chat = {
      agent_preset_id: "preset-1",
      agent_preset_version_id: "version-1",
    } as AgentSessionsGetSessionVercelResponse

    const { result } = renderHook(() =>
      useChatPresetManager({
        workspaceId: "workspace-1",
        chat,
        updateChat: jest.fn(),
        isUpdatingChat: false,
        chatLoading: false,
        selectedChatId: "chat-1",
      })
    )

    expect(result.current.selectedPresetConfig).toBe(presetVersion)
    expect(result.current.selectedPresetConfigError).toBeNull()
  })
})
