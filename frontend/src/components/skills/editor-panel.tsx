"use client"

import {
  Download,
  Loader2,
  Plus,
  RefreshCcw,
  Trash2,
  Upload,
} from "lucide-react"
import dynamic from "next/dynamic"
import { type ChangeEvent, useMemo, useRef } from "react"
import type { SkillDraftFileRead, SkillDraftRead, SkillRead } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SkillFileTree } from "@/components/skills/file-tree"
import { SkillFileIcon } from "@/components/skills/skill-file-icon"

const CodeEditor = dynamic(
  () =>
    import("@/components/editor/codemirror/code-editor").then(
      (m) => m.CodeEditor
    ),
  { ssr: false }
)
const SimpleEditor = dynamic(
  () =>
    import("@/components/tiptap-templates/simple/simple-editor").then(
      (m) => m.SimpleEditor
    ),
  { ssr: false }
)

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  composeMarkdownFrontmatter,
  splitMarkdownFrontmatter,
} from "@/lib/markdown-frontmatter"
import type { VisibleFileEntry } from "@/lib/skills-studio"
import {
  buildSkillFileTree,
  getLanguageForPath,
  isEditablePath,
  isMarkdownPath,
} from "@/lib/skills-studio"

type EditorPanelProps = {
  skill?: SkillRead
  skillLoading: boolean
  draft?: SkillDraftRead
  draftLoading: boolean
  activeSkillId: string | null
  visibleFiles: VisibleFileEntry[]
  selectedFile: VisibleFileEntry | null
  selectedPath: string | null
  draftFile?: SkillDraftFileRead
  draftFileLoading: boolean
  currentTextValue: string | null
  markdownEditorActivatedRef: React.MutableRefObject<boolean>
  onSelectPath: (path: string) => void
  onEditorChange: (nextValue: string) => void
  onDeleteSelectedFile: () => void
  onUndoSelectedFileChange: () => void
  onReplaceFile: (event: ChangeEvent<HTMLInputElement>) => void
  onSaveWorkingCopy: () => Promise<void>
  onOpenNewFileDialog: () => void
  onOpenNewSkillDialog: () => void
  onOpenUploadSkillDialog: () => void
}

/**
 * Center pane with file tree sidebar and code/markdown editor.
 *
 * @param props Panel state and callbacks from the parent hook.
 */
export function EditorPanel({
  skill,
  skillLoading,
  draft,
  draftLoading,
  activeSkillId,
  visibleFiles,
  selectedFile,
  selectedPath,
  draftFile,
  draftFileLoading,
  currentTextValue,
  markdownEditorActivatedRef,
  onSelectPath,
  onEditorChange,
  onDeleteSelectedFile,
  onUndoSelectedFileChange,
  onReplaceFile,
  onSaveWorkingCopy,
  onOpenNewFileDialog,
  onOpenNewSkillDialog,
  onOpenUploadSkillDialog,
}: EditorPanelProps) {
  const replaceInputRef = useRef<HTMLInputElement>(null)
  const fileTree = useMemo(
    () => buildSkillFileTree(visibleFiles),
    [visibleFiles]
  )
  const splitFrontmatter =
    selectedFile !== null &&
    isMarkdownPath(selectedFile.path) &&
    currentTextValue !== null
      ? splitMarkdownFrontmatter(currentTextValue)
      : null

  if (skillLoading || draftLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <CenteredSpinner />
      </div>
    )
  }

  if (!activeSkillId || !skill || !draft) {
    return (
      <div className="flex h-full items-center justify-center px-8 text-center">
        <div className="max-w-md space-y-4">
          <div className="space-y-2">
            <h3 className="text-lg font-semibold">Build a skill</h3>
            <p className="text-sm text-muted-foreground">
              Create a skill from scratch or upload an existing skill directory
              to get started.
            </p>
          </div>
          <div className="flex items-center justify-center gap-2">
            <Button onClick={onOpenNewSkillDialog}>
              <Plus className="mr-2 size-4" />
              Create skill
            </Button>
            <Button variant="outline" onClick={onOpenUploadSkillDialog}>
              <Upload className="mr-2 size-4" />
              Upload
            </Button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <ResizablePanelGroup
      direction="horizontal"
      className="flex h-full min-h-0 overflow-hidden"
    >
      {/* File tree sidebar */}
      <ResizablePanel
        defaultSize={20}
        minSize={12}
        maxSize={40}
        className="flex h-full min-h-0 flex-col"
      >
        <div className="flex items-center justify-between border-b px-3 py-2">
          <div className="min-w-0">
            <div className="text-sm font-medium">Files</div>
            <div className="truncate text-xs text-muted-foreground">
              Draft files
            </div>
          </div>
          <Button size="icon" variant="ghost" onClick={onOpenNewFileDialog}>
            <Plus className="size-4" />
          </Button>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <div className="p-2">
            <SkillFileTree
              nodes={fileTree}
              selectedPath={selectedPath}
              onSelectPath={onSelectPath}
            />
          </div>
        </ScrollArea>
      </ResizablePanel>

      <ResizableHandle />

      {/* Editor area */}
      <ResizablePanel
        defaultSize={80}
        className="flex min-h-0 flex-col overflow-hidden"
      >
        <div className="flex items-center justify-between border-b px-4 py-2">
          <div>
            <div className="flex items-center gap-2 text-sm font-medium">
              {selectedFile ? <SkillFileIcon path={selectedFile.path} /> : null}
              <span>{selectedFile?.path ?? "Select a file"}</span>
            </div>
            <div className="text-xs text-muted-foreground">
              {selectedFile
                ? `${selectedFile.contentType} · ${selectedFile.sizeBytes} bytes`
                : "Markdown and Python files can be edited inline."}
            </div>
          </div>
          <div className="flex items-center gap-1">
            {selectedFile ? (
              <>
                {selectedFile.change ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={onUndoSelectedFileChange}
                  >
                    <RefreshCcw className="mr-2 size-4" />
                    Reset
                  </Button>
                ) : null}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => replaceInputRef.current?.click()}
                >
                  <Upload className="mr-2 size-4" />
                  Replace
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onDeleteSelectedFile}
                >
                  <Trash2 className="mr-2 size-4" />
                  Delete
                </Button>
              </>
            ) : null}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <div className="flex min-h-full flex-col p-4">
            {!selectedFile ? (
              <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                Select a file to start editing.
              </div>
            ) : selectedFile.change?.kind === "delete" ? (
              <div className="flex flex-1 items-center justify-center">
                <Alert className="max-w-md">
                  <Trash2 className="size-4" />
                  <AlertTitle>Marked for deletion</AlertTitle>
                  <AlertDescription>
                    Save the working copy to remove this file from the skill.
                  </AlertDescription>
                </Alert>
              </div>
            ) : selectedFile.change?.kind === "upload" ? (
              <div className="flex flex-1 items-center justify-center">
                <Alert className="max-w-md">
                  <Upload className="size-4" />
                  <AlertTitle>Replacement pending</AlertTitle>
                  <AlertDescription>
                    Save the working copy to upload and attach the replacement
                    file.
                  </AlertDescription>
                </Alert>
              </div>
            ) : draftFileLoading ? (
              <div className="flex flex-1 items-center justify-center">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : currentTextValue !== null &&
              isEditablePath(selectedFile.path) ? (
              isMarkdownPath(selectedFile.path) && splitFrontmatter ? (
                <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-4">
                  <div
                    className="flex flex-col gap-2"
                    onFocusCapture={() => {
                      markdownEditorActivatedRef.current = true
                    }}
                  >
                    <div>
                      <div className="text-sm font-medium">Frontmatter</div>
                      <div className="text-xs text-muted-foreground">
                        Metadata the agent uses to discover this skill. Requires{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-[11px]">
                          name
                        </code>{" "}
                        and{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-[11px]">
                          description
                        </code>
                        .
                      </div>
                    </div>
                    <CodeEditor
                      value={splitFrontmatter.frontmatter}
                      onChange={(nextFrontmatter) =>
                        onEditorChange(
                          composeMarkdownFrontmatter(
                            nextFrontmatter,
                            splitFrontmatter.body
                          )
                        )
                      }
                      language="yaml"
                    />
                  </div>
                  <div className="flex flex-1 flex-col">
                    <div className="mb-2">
                      <div className="text-sm font-medium">Instructions</div>
                      <div className="text-xs text-muted-foreground">
                        Workflow, examples, and references the agent loads when
                        the skill is triggered.
                      </div>
                    </div>
                    <SimpleEditor
                      value={splitFrontmatter.body}
                      onChange={(nextBody) =>
                        onEditorChange(
                          composeMarkdownFrontmatter(
                            splitFrontmatter.frontmatter,
                            nextBody
                          )
                        )
                      }
                      onSave={() => void onSaveWorkingCopy()}
                      onFocus={() => {
                        markdownEditorActivatedRef.current = true
                      }}
                      className="flex-1"
                    />
                  </div>
                </div>
              ) : isMarkdownPath(selectedFile.path) ? (
                <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col">
                  <SimpleEditor
                    value={currentTextValue}
                    onChange={onEditorChange}
                    onSave={() => void onSaveWorkingCopy()}
                    onFocus={() => {
                      markdownEditorActivatedRef.current = true
                    }}
                    className="flex-1"
                  />
                </div>
              ) : (
                <CodeEditor
                  value={currentTextValue}
                  onChange={onEditorChange}
                  language={getLanguageForPath(selectedFile.path)}
                  className="flex-1"
                />
              )
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <Alert className="max-w-md">
                  <Download className="size-4" />
                  <AlertTitle>Read-only file</AlertTitle>
                  <AlertDescription className="space-y-3">
                    <p>
                      This file type is not editable inline in v1. You can
                      replace it, delete it, or download the current blob.
                    </p>
                    {draftFile?.kind === "download" &&
                    draftFile.download_url ? (
                      <Button asChild size="sm" variant="outline">
                        <a
                          href={draftFile.download_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          <Download className="mr-2 size-4" />
                          Download current file
                        </a>
                      </Button>
                    ) : null}
                  </AlertDescription>
                </Alert>
              </div>
            )}
          </div>
        </div>

        <input
          ref={replaceInputRef}
          type="file"
          className="hidden"
          onChange={(event) => void onReplaceFile(event)}
        />
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}
