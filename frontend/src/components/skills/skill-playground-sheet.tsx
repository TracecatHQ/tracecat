"use client"

import { AlertCircle, Bot, Loader2 } from "lucide-react"
import dynamic from "next/dynamic"
import type {
  AgentSessionEntity,
  AgentSessionsGetSessionVercelResponse,
  AgentSessionsListSessionsResponse,
  ApiError,
  MCPIntegrationRead,
  SkillDraftRead,
  SkillRead,
  SkillVersionRead,
} from "@/client"
import { ChatHistoryDropdown } from "@/components/chat/chat-history-dropdown"

const ChatSessionPane = dynamic(
  () =>
    import("@/components/chat/chat-session-pane").then(
      (m) => m.ChatSessionPane
    ),
  { ssr: false }
)

import { MultiTagCommandInput } from "@/components/tags-input"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import type { ModelInfo } from "@/lib/chat"
import { getApiErrorDetail } from "@/lib/errors"
import { describeVersion } from "@/lib/skills-studio"

type SkillPlaygroundSheetProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  activeSkillId: string | null
  skill?: SkillRead
  draft?: SkillDraftRead
  hasUnsavedChanges: boolean
  versions?: SkillVersionRead[]
  selectedVersionId: string | null
  onSelectVersionId: (versionId: string) => void
  chatReady: boolean
  chatReadinessLoading: boolean
  chatReason?: string
  modelInfo?: ModelInfo
  playgroundPrompt: string
  onPlaygroundPromptChange: (value: string) => void
  playgroundMcpIds: string[]
  onPlaygroundMcpIdsChange: (value: string[]) => void
  mcpIntegrations?: MCPIntegrationRead[]
  mcpIntegrationsIsLoading: boolean
  createSkillPlaygroundSessionPending: boolean
  onCreatePlaygroundSession: () => Promise<void>
  chats?: AgentSessionsListSessionsResponse
  chatsLoading: boolean
  chatsError: ApiError | null
  activeChatId: string | null
  onSelectChat: (chatId: string) => void
  chat?: AgentSessionsGetSessionVercelResponse
  chatLoading: boolean
  chatError: ApiError | null
}

/**
 * Slide-over playground for testing a published skill version.
 *
 * @param props Sheet state, playground state, and callbacks.
 * @returns A right-side sheet containing validation issues and chat controls.
 *
 * @example
 * <SkillPlaygroundSheet
 *   open={open}
 *   onOpenChange={setOpen}
 *   workspaceId={workspaceId}
 *   activeSkillId={skillId}
 *   hasUnsavedChanges={false}
 *   selectedVersionId={versionId}
 *   onSelectVersionId={setVersionId}
 *   chatReady
 *   chatReadinessLoading={false}
 *   playgroundPrompt=""
 *   onPlaygroundPromptChange={() => {}}
 *   playgroundMcpIds={[]}
 *   onPlaygroundMcpIdsChange={() => {}}
 *   mcpIntegrationsIsLoading={false}
 *   createSkillPlaygroundSessionPending={false}
 *   onCreatePlaygroundSession={async () => {}}
 *   chatsLoading={false}
 *   chatsError={null}
 *   activeChatId={null}
 *   onSelectChat={() => {}}
 *   chatLoading={false}
 *   chatError={null}
 * />
 */
export function SkillPlaygroundSheet({
  open,
  onOpenChange,
  workspaceId,
  activeSkillId,
  skill,
  draft,
  hasUnsavedChanges,
  versions,
  selectedVersionId,
  onSelectVersionId,
  chatReady,
  chatReadinessLoading,
  chatReason,
  modelInfo,
  playgroundPrompt,
  onPlaygroundPromptChange,
  playgroundMcpIds,
  onPlaygroundMcpIdsChange,
  mcpIntegrations,
  mcpIntegrationsIsLoading,
  createSkillPlaygroundSessionPending,
  onCreatePlaygroundSession,
  chats,
  chatsLoading,
  chatsError,
  activeChatId,
  onSelectChat,
  chat,
  chatLoading,
  chatError,
}: SkillPlaygroundSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex h-full w-[560px] flex-col gap-0 p-0 sm:max-w-[560px]"
      >
        <SheetHeader className="border-b px-6 py-4">
          <SheetTitle>Skill playground</SheetTitle>
          <SheetDescription>
            Test a published skill version without leaving the editor.
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="flex-1">
          <div className="space-y-6 p-6">
            {draft?.validation_errors?.length ? (
              <>
                <section className="space-y-3">
                  <div>
                    <h3 className="text-sm font-semibold">Validation issues</h3>
                    <p className="text-xs text-muted-foreground">
                      Fix these before publishing a new version.
                    </p>
                  </div>
                  <div className="space-y-2">
                    {(draft.validation_errors ?? []).map((error) => (
                      <Alert
                        key={`${error.code}-${error.path ?? "root"}`}
                        variant="destructive"
                      >
                        <AlertCircle className="size-4" />
                        <AlertTitle>{error.path ?? error.code}</AlertTitle>
                        <AlertDescription>{error.message}</AlertDescription>
                      </Alert>
                    ))}
                  </div>
                </section>

                <Separator />
              </>
            ) : null}

            {!skill?.current_version_id ? (
              <Alert>
                <AlertCircle className="size-4" />
                <AlertTitle>No published version</AlertTitle>
                <AlertDescription>
                  Publish the working copy before you start a playground
                  session.
                </AlertDescription>
              </Alert>
            ) : chatReadinessLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" />
                Checking model readiness…
              </div>
            ) : !chatReady || !modelInfo ? (
              <Alert variant="destructive">
                <AlertCircle className="size-4" />
                <AlertTitle>
                  {chatReason === "no_model"
                    ? "No default model"
                    : "Missing credentials"}
                </AlertTitle>
                <AlertDescription>
                  {chatReason === "no_model"
                    ? "Select a default model in agent settings to enable skill testing."
                    : `Configure ${modelInfo?.provider ?? "the selected provider"} credentials to enable skill testing.`}
                </AlertDescription>
              </Alert>
            ) : (
              <div className="space-y-3">
                <div className="space-y-2">
                  <Label htmlFor="playground-version">Published version</Label>
                  <select
                    id="playground-version"
                    value={selectedVersionId ?? ""}
                    onChange={(event) => onSelectVersionId(event.target.value)}
                    className="flex h-9 w-full rounded-md border bg-background px-3 text-sm"
                  >
                    {(versions ?? []).map((version) => (
                      <option key={version.id} value={version.id}>
                        {describeVersion(version)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="playground-prompt">
                    System prompt override
                  </Label>
                  <Textarea
                    id="playground-prompt"
                    value={playgroundPrompt}
                    onChange={(event) =>
                      onPlaygroundPromptChange(event.target.value)
                    }
                    placeholder="Optional playground-specific instructions"
                    rows={4}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Allowed MCP integrations</Label>
                  <MultiTagCommandInput
                    value={playgroundMcpIds}
                    onChange={onPlaygroundMcpIdsChange}
                    suggestions={(mcpIntegrations ?? []).map((integration) => ({
                      id: integration.id,
                      value: integration.id,
                      label: integration.name,
                      description: integration.description || "MCP integration",
                    }))}
                    placeholder={
                      mcpIntegrationsIsLoading
                        ? "Loading integrations..."
                        : "Select MCP integrations"
                    }
                    searchKeys={["label", "value", "description"]}
                    disabled={mcpIntegrationsIsLoading}
                  />
                </div>
                <div className="flex items-center justify-between gap-2 rounded-md border p-3 text-xs">
                  <div>
                    <div className="font-medium">Model: {modelInfo.name}</div>
                    <div className="text-muted-foreground">
                      Provider: {modelInfo.provider}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => void onCreatePlaygroundSession()}
                    disabled={
                      !selectedVersionId ||
                      hasUnsavedChanges ||
                      createSkillPlaygroundSessionPending
                    }
                  >
                    {createSkillPlaygroundSessionPending ? (
                      <Loader2 className="mr-2 size-4 animate-spin" />
                    ) : (
                      <Bot className="mr-2 size-4" />
                    )}
                    New chat
                  </Button>
                </div>
                {hasUnsavedChanges ? (
                  <Alert>
                    <AlertCircle className="size-4" />
                    <AlertTitle>Publish to test latest changes</AlertTitle>
                    <AlertDescription>
                      Playground sessions only run published versions. Save and
                      publish the working copy first if you want to test the
                      latest edits.
                    </AlertDescription>
                  </Alert>
                ) : null}

                <div className="overflow-hidden rounded-md border">
                  <div className="flex items-center justify-between border-b px-3 py-2">
                    <div className="text-sm font-medium">Session</div>
                    <ChatHistoryDropdown
                      chats={chats}
                      isLoading={chatsLoading}
                      error={chatsError}
                      selectedChatId={activeChatId ?? undefined}
                      onSelectChat={onSelectChat}
                      align="end"
                    />
                  </div>
                  <div className="h-[520px]">
                    {!activeChatId ? (
                      <div className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">
                        Start a playground chat to QA the selected published
                        version.
                      </div>
                    ) : chatLoading ? (
                      <div className="flex h-full items-center justify-center">
                        <Loader2 className="size-5 animate-spin text-muted-foreground" />
                      </div>
                    ) : chatError || !chat ? (
                      <Alert variant="destructive" className="m-4">
                        <AlertCircle className="size-4" />
                        <AlertTitle>Chat unavailable</AlertTitle>
                        <AlertDescription>
                          {getApiErrorDetail(chatError) ??
                            "Failed to load the playground session."}
                        </AlertDescription>
                      </Alert>
                    ) : (
                      <ChatSessionPane
                        chat={chat}
                        workspaceId={workspaceId}
                        entityType={"skill" as AgentSessionEntity}
                        entityId={activeSkillId ?? undefined}
                        className="h-full"
                        placeholder={`Test ${skill?.title ?? skill?.slug ?? "skill"}…`}
                        modelInfo={modelInfo as ModelInfo}
                        toolsEnabled={false}
                      />
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
