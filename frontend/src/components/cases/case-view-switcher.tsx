"use client"

import {
  AlertTriangleIcon,
  CheckIcon,
  LayersIcon,
  LinkIcon,
  LockIcon,
  PencilIcon,
  PinIcon,
  PinOffIcon,
  PlusIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react"
import { usePathname } from "next/navigation"
import { type FormEvent, type ReactNode, useRef, useState } from "react"
import type { CaseViewRead, CaseViewVisibility } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import type { CaseViewControls } from "@/hooks/use-case-view-state"
import { useCaseViewMutations, useCaseViews } from "@/hooks/use-case-views"
import { cn } from "@/lib/utils"
import { useHasScope } from "@/providers/scopes"

/** What the inline view form is doing, or null when it is closed. */
export type CaseViewEditorMode =
  | { kind: "create" }
  | { kind: "duplicate"; view: CaseViewRead }
  | { kind: "edit"; view: CaseViewRead }

/** The open state of the inline view form, shared by tabs, form, and actions. */
export interface CaseViewEditor {
  mode: CaseViewEditorMode | null
  open: (mode: CaseViewEditorMode) => void
  close: () => void
}

/** Track the inline form that names, creates, and edits case views. */
export function useCaseViewEditor(): CaseViewEditor {
  const [mode, setMode] = useState<CaseViewEditorMode | null>(null)
  return { mode, open: setMode, close: () => setMode(null) }
}

interface CaseViewMenuProps {
  workspaceId: string
  controls: CaseViewControls
  editor: CaseViewEditor
}

/**
 * The "Custom case views" icon next to the page title.
 *
 * It lists every view the user can open, and holds the actions for the open
 * view: edit, duplicate, pin, copy link, and delete.
 */
export function CaseViewMenu({
  workspaceId,
  controls,
  editor,
}: CaseViewMenuProps) {
  const { active } = controls
  const view = active.view
  const canReadCases = useHasScope("case:read") === true
  const views = useCaseViews(workspaceId, canReadCases)
  const { deleteView, pinView, unpinView } = useCaseViewMutations(workspaceId)
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  // A form opened from the menu waits for the menu to close, because the
  // menu returns focus to its button and would take it from the name field.
  const pendingEditorRef = useRef<CaseViewEditorMode | null>(null)
  // The dialog's open state is separate from its target, so the text stays
  // filled in while the dialog animates closed.
  const [deleteTarget, setDeleteTarget] = useState<CaseViewRead | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)

  function select(viewId: string | null) {
    setOpen(false)
    editor.close()
    if (viewId !== active.viewId) {
      active.selectView(viewId)
    }
  }

  function openEditor(mode: CaseViewEditorMode) {
    pendingEditorRef.current = mode
    setOpen(false)
  }

  function togglePin(target: CaseViewRead) {
    if (target.pinned) {
      unpinView.mutate(target.id)
    } else {
      pinView.mutate(target.id)
    }
  }

  async function copyLink(target: CaseViewRead) {
    setOpen(false)
    const url = `${window.location.origin}${pathname}?view=${encodeURIComponent(target.id)}`
    try {
      await navigator.clipboard.writeText(url)
      toast({ title: "Link copied" })
    } catch {
      toast({ title: "Could not copy the link", variant: "destructive" })
    }
  }

  function renderItem(item: CaseViewRead) {
    const isActive = active.viewId === item.id
    return (
      <CommandItem
        key={item.id}
        value={`${item.name} ${item.id}`}
        onSelect={() => select(item.id)}
        className="group flex items-center gap-2 text-xs"
      >
        <CheckIcon
          className={cn("size-3.5 shrink-0", !isActive && "invisible")}
        />
        <span className="truncate">{item.name}</span>
        <button
          type="button"
          aria-label={item.pinned ? "Unpin from sidebar" : "Pin to sidebar"}
          title={item.pinned ? "Unpin from sidebar" : "Pin to sidebar"}
          className={cn(
            "ml-auto rounded p-0.5 text-muted-foreground hover:text-foreground",
            !item.pinned && "opacity-0 group-hover:opacity-100"
          )}
          onClick={(event) => {
            event.stopPropagation()
            togglePin(item)
          }}
        >
          <PinIcon className={cn("size-3", item.pinned && "fill-current")} />
        </button>
      </CommandItem>
    )
  }

  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label="Custom case views"
                className={cn(
                  "flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  active.viewId !== null && "bg-muted text-foreground"
                )}
              >
                <LayersIcon className="size-3.5" />
              </button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">Custom case views</TooltipContent>
        </Tooltip>
        <PopoverContent
          className="w-64 p-0 shadow-sm"
          align="start"
          onCloseAutoFocus={(event) => {
            const pending = pendingEditorRef.current
            pendingEditorRef.current = null
            if (pending) {
              event.preventDefault()
              editor.open(pending)
            }
          }}
        >
          <Command>
            <CommandInput placeholder="Find a view..." className="text-xs" />
            <CommandList>
              <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
                No matching views.
              </CommandEmpty>
              <CommandGroup heading="Built-in">
                <CommandItem
                  value="all cases"
                  onSelect={() => select(null)}
                  className="flex items-center gap-2 text-xs"
                >
                  <CheckIcon
                    className={cn(
                      "size-3.5 shrink-0",
                      active.viewId !== null && "invisible"
                    )}
                  />
                  All cases
                </CommandItem>
                {views.builtins.map(renderItem)}
              </CommandGroup>
              {views.personal.length > 0 && (
                <CommandGroup heading="My views">
                  {views.personal.map(renderItem)}
                </CommandGroup>
              )}
              {views.shared.length > 0 && (
                <CommandGroup heading="Shared with workspace">
                  {views.shared.map(renderItem)}
                </CommandGroup>
              )}
              {view?.kind === "custom" && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading={view.name}>
                    {view.can_edit && (
                      <CommandItem
                        value="action edit view"
                        onSelect={() => openEditor({ kind: "edit", view })}
                        className="gap-2 text-xs"
                      >
                        <PencilIcon className="size-3.5" />
                        Edit view...
                      </CommandItem>
                    )}
                    <CommandItem
                      value="action duplicate view"
                      disabled={controls.saveBlockedReason !== null}
                      title={controls.saveBlockedReason ?? undefined}
                      onSelect={() => openEditor({ kind: "duplicate", view })}
                      className="gap-2 text-xs"
                    >
                      <LayersIcon className="size-3.5" />
                      Duplicate...
                    </CommandItem>
                    <CommandItem
                      value="action pin view sidebar"
                      onSelect={() => togglePin(view)}
                      className="gap-2 text-xs"
                    >
                      {view.pinned ? (
                        <PinOffIcon className="size-3.5" />
                      ) : (
                        <PinIcon className="size-3.5" />
                      )}
                      {view.pinned ? "Unpin from sidebar" : "Pin to sidebar"}
                    </CommandItem>
                    <CommandItem
                      value="action copy view link"
                      onSelect={() => void copyLink(view)}
                      className="gap-2 text-xs"
                    >
                      <LinkIcon className="size-3.5" />
                      Copy link
                    </CommandItem>
                    {view.can_edit && (
                      <CommandItem
                        value="action delete view"
                        onSelect={() => {
                          setOpen(false)
                          setDeleteTarget(view)
                          setDeleteOpen(true)
                        }}
                        className="gap-2 text-xs text-destructive"
                      >
                        <Trash2Icon className="size-3.5" />
                        Delete
                      </CommandItem>
                    )}
                  </CommandGroup>
                </>
              )}
              <CommandSeparator />
              <CommandGroup>
                <CommandItem
                  value="action new view from the current filters"
                  disabled={controls.saveBlockedReason !== null}
                  title={controls.saveBlockedReason ?? undefined}
                  onSelect={() => openEditor({ kind: "create" })}
                  className="gap-2 text-xs"
                >
                  <PlusIcon className="size-3.5" />
                  New view
                </CommandItem>
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      {active.isError && (
        <AlertTriangleIcon
          role="img"
          aria-label="This view could not be refreshed"
          className="size-3.5 text-muted-foreground"
        />
      )}

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete view</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.visibility === "workspace"
                ? `Delete "${deleteTarget?.name}" for everyone in the workspace? Cases are not affected.`
                : `Delete "${deleteTarget?.name}"? Cases are not affected.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (!deleteTarget) return
                await deleteView.mutateAsync(deleteTarget.id)
                if (active.viewId === deleteTarget.id) {
                  active.selectView(null)
                }
                setDeleteOpen(false)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

interface CaseViewEditorPanelProps {
  workspaceId: string
  controls: CaseViewControls
  editor: CaseViewEditor
  mode: CaseViewEditorMode
}

function initialName(mode: CaseViewEditorMode): string {
  switch (mode.kind) {
    case "edit":
      return mode.view.name
    case "duplicate":
      return `${mode.view.name} (copy)`
    default:
      return ""
  }
}

/**
 * The inline form under the view tabs for naming a new view, or renaming and
 * re-sharing an existing one.
 */
export function CaseViewEditorPanel({
  workspaceId,
  controls,
  editor,
  mode,
}: CaseViewEditorPanelProps) {
  const { user } = useAuth()
  const canShare = useHasScope("case:update") === true
  const [name, setName] = useState(() => initialName(mode))
  const [visibility, setVisibility] = useState<CaseViewVisibility>(
    mode.kind === "edit" ? mode.view.visibility : "personal"
  )
  const isEdit = mode.kind === "edit"
  const editView = isEdit ? mode.view : null
  const { updateView } = useCaseViewMutations(workspaceId)
  const isPending = controls.isSaving || updateView.isPending

  // Sharing needs case:update. Only the owner can take a shared view back.
  const canPickWorkspace = canShare || editView?.visibility === "workspace"
  const canPickPersonal =
    !editView ||
    editView.visibility === "personal" ||
    editView.owner_id === user?.id

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed || isPending) return
    if (editView) {
      await updateView.mutateAsync({
        viewId: editView.id,
        requestBody: {
          name: trimmed,
          ...(visibility !== editView.visibility ? { visibility } : {}),
        },
      })
    } else {
      await controls.saveAsNew(trimmed, visibility)
    }
    editor.close()
  }

  return (
    <form
      aria-label={isEdit ? "Edit view" : "New view"}
      onSubmit={handleSubmit}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault()
          editor.close()
        }
      }}
      className="flex items-start gap-3 border-b px-4 py-3"
    >
      <div className="flex size-7 shrink-0 items-center justify-center rounded-md border border-input text-muted-foreground">
        <LayersIcon className="size-3.5" />
      </div>
      <div className="min-w-0 flex-1 space-y-2">
        <input
          autoFocus
          aria-label="View name"
          maxLength={255}
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="View name"
          className="-mx-1 w-[calc(100%+0.5rem)] rounded-sm bg-transparent px-1 text-sm font-medium outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        />
        <div className="flex flex-wrap items-center gap-3">
          <fieldset className="flex items-center gap-0.5 rounded-md border border-input p-0.5">
            <legend className="sr-only">Who can see this view</legend>
            <VisibilityOption
              label="Only me"
              icon={<LockIcon className="size-3" />}
              selected={visibility === "personal"}
              disabledReason={
                canPickPersonal
                  ? null
                  : "Only the owner can make a shared view personal."
              }
              onSelect={() => setVisibility("personal")}
            />
            <VisibilityOption
              label="Workspace"
              icon={<UsersIcon className="size-3" />}
              selected={visibility === "workspace"}
              disabledReason={
                canPickWorkspace
                  ? null
                  : "Sharing needs permission to update cases."
              }
              onSelect={() => setVisibility("workspace")}
            />
          </fieldset>
          <span className="text-xs text-muted-foreground">
            {isEdit
              ? "Filters are saved from the filter bar."
              : "Saves the current filters, sort, and columns."}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          onClick={editor.close}
        >
          Cancel
        </Button>
        <Button
          type="submit"
          size="sm"
          className="h-7 px-2.5 text-xs"
          disabled={isPending || !name.trim()}
        >
          {isEdit ? "Save" : "Create view"}
        </Button>
      </div>
    </form>
  )
}

function VisibilityOption({
  label,
  icon,
  selected,
  disabledReason,
  onSelect,
}: {
  label: string
  icon: ReactNode
  selected: boolean
  disabledReason: string | null
  onSelect: () => void
}) {
  const button = (
    <button
      type="button"
      aria-pressed={selected}
      disabled={disabledReason !== null}
      onClick={onSelect}
      className={cn(
        "flex h-5 items-center gap-1 rounded px-1.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        selected
          ? "bg-muted text-foreground"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {icon}
      {label}
    </button>
  )
  if (disabledReason === null) return button
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span>{button}</span>
      </TooltipTrigger>
      <TooltipContent>{disabledReason}</TooltipContent>
    </Tooltip>
  )
}

interface CaseViewActionsProps {
  controls: CaseViewControls
  editor: CaseViewEditor
  hasFilters: boolean
  clearFilters: () => void
}

/**
 * The buttons at the end of the filter row.
 *
 * Clear shows whenever filters are applied, with or without a view open.
 * Without a view, Save view follows it. With a changed view: Reset, Save as
 * new, and Save when the user may edit it.
 */
export function CaseViewActions({
  controls,
  editor,
  hasFilters,
  clearFilters,
}: CaseViewActionsProps) {
  const { active, isModified, isSaving, saveBlockedReason } = controls
  const view = active.view
  const cannotSave = isSaving || saveBlockedReason !== null
  const blockedTitle = saveBlockedReason ?? undefined

  return (
    <>
      {hasFilters && (
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs"
          onClick={clearFilters}
        >
          Clear
        </Button>
      )}
      {!view && hasFilters && (
        <Button
          size="sm"
          variant="outline"
          className="h-6 px-2 text-xs"
          disabled={cannotSave || editor.mode !== null}
          title={blockedTitle}
          onClick={() => editor.open({ kind: "create" })}
        >
          Save view
        </Button>
      )}
      {view && isModified && (
        <>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            onClick={controls.reset}
          >
            Reset
          </Button>
          <Button
            size="sm"
            variant={view.can_edit ? "ghost" : "outline"}
            className="h-6 px-2 text-xs"
            disabled={cannotSave || editor.mode !== null}
            title={blockedTitle}
            onClick={() => editor.open({ kind: "duplicate", view })}
          >
            Save as new
          </Button>
          {view.can_edit && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              disabled={cannotSave}
              title={blockedTitle}
              onClick={() => void controls.save()}
            >
              Save
            </Button>
          )}
        </>
      )}
    </>
  )
}

/** Read-only chips for view conditions the filter bar cannot show. */
export function CaseViewChips({ controls }: { controls: CaseViewControls }) {
  const { view, mapped } = controls.active
  const staleCount = view?.stale_refs.length ?? 0
  if (!mapped || (mapped.extraLabels.length === 0 && staleCount === 0)) {
    return null
  }
  return (
    <>
      {mapped.extraLabels.map((label) => (
        <span
          key={label}
          title="Part of this view. Edit it through the API."
          className="flex h-6 items-center rounded-md border border-dashed px-2 text-xs text-muted-foreground"
        >
          {label}
        </span>
      ))}
      {staleCount > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex h-6 items-center gap-1 rounded-md border border-dashed px-2 text-xs text-muted-foreground">
              <AlertTriangleIcon className="size-3" />
              {staleCount === 1
                ? "1 deleted tag or option"
                : `${staleCount} deleted tags or options`}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            This view names{" "}
            {view?.stale_refs.map((ref) => ref.value).join(", ")}, which no
            longer exist. A filter left with nothing to match shows no cases.
            Update the filters and save the view to fix it.
          </TooltipContent>
        </Tooltip>
      )}
    </>
  )
}
