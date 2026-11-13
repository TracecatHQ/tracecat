import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import type { AgentPresetRead } from "@/client"
import { AgentChannelsPanel } from "@/components/agents/external-channels/agent-channels-panel"
import {
  PRESET,
  WORKSPACE_ID,
} from "@/components/agents/external-channels/channel-bot-fixtures"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useEntitlements } from "@/hooks/use-entitlements"
import { QueryClient, QueryClientProvider } from "@/lib/query"

// Mounts as the preset builder does: once entitlements turn channels on.
function ChannelsTab({ preset }: { preset: AgentPresetRead | null }) {
  const { hasEntitlement } = useEntitlements()
  return hasEntitlement("agent_channels") ? (
    <AgentChannelsPanel workspaceId={WORKSPACE_ID} preset={preset} />
  ) : null
}

/** Renders the Channels tab with a fresh cache. */
export function renderPanel(
  preset: AgentPresetRead | null = PRESET
): ReturnType<typeof render> & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    )
  }
  const view = render(<ChannelsTab preset={preset} />, { wrapper: Wrapper })
  return { ...view, client }
}

/** A provider row's title, which is also its accessible name. */
export type ProviderTitle = "Custom Slack app"

/** Opens a provider's dialog from its row, once the bot list has loaded. */
export async function openProvider(
  title: ProviderTitle,
  action: "Connect" | "Manage"
): Promise<void> {
  const row = await screen.findByRole("group", { name: title })
  await userEvent.click(within(row).getByRole("button", { name: action }))
}
