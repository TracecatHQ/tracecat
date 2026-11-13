"use client"

import {
  AlertTriangleIcon,
  CheckIcon,
  ChevronDown,
  LayersIcon,
  MoreHorizontalIcon,
  PinIcon,
  PinOffIcon,
  PlusIcon,
} from "lucide-react"
import { type FormEvent, useState } from "react"
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
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useAuth } from "@/hooks/use-auth"
import type { CaseViewControls } from "@/hooks/use-case-view-state"
import { useCaseViewMutations, useCaseViews } from "@/hooks/use-case-views"
import { cn } from "@/lib/utils"
import { useHasScope } from "@/providers/scopes"

const SAVE_VIEW_FORM_ID = "case-view-save-form"

type SaveDialogMode =
  | { kind: "create" }
  | { kind: "duplicate"; view: CaseViewRead }
  | { kind: "rename"; view: CaseViewRead }

interface CaseViewSwitcherProps {
  workspaceId: string
  controls: CaseViewControls
}

/** The saved view picker, its actions, and the open view's save buttons. */
export function CaseViewSwitcher({
  workspaceId,
  controls,
}: CaseViewSwitcherProps) {
  const { active, isModified, isSaving } = controls
  const views = useCaseViews(workspaceId)
  const { updateView, deleteView, pinView, unpinView } =
    useCaseViewMutations(workspaceId)
  const canShare = useHasScope("case:update") === true
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const [dialog, setDialog] = useState<SaveDialogMode | null>(null)
  const [pendingDelete, setPendingDelete] = useState<CaseViewRead | null>(null)

  const view = active.view
  const label = view?.name ?? (active.viewId ? "Loading view…" : "Cases")

  function choose(viewId: string | null) {
    setOpen(false)
    active.selectView(viewId)
  }

  function togglePin(target: CaseViewRead) {
    if (target.pinned) {
      unpinView.mutate(target.id)
    } else {
      pinView.mutate(target.id)
    }
  }

  function renderItem(item: CaseViewRead) {
    const isActive = active.viewId === item.id
    return (
      <CommandItem
        key={item.id}
        value={`${item.name} ${item.id}`}
        onSelect={() => choose(item.id)}
        className="group flex items-center gap-2 text-xs"
      >
        <CheckIcon
          className={cn("size-3.5 shrink-0", !isActive && "invisible")}
        />
        <span className="truncate">{item.name}</span>
        {item.kind === "custom" && (
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
        )}
      </CommandItem>
    )
  }

  return (
    <div className="flex shrink-0 items-center gap-1.5">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label="Case views"
            className="flex h-6 max-w-[220px] items-center gap-1.5 rounded-md px-1.5 text-xs font-medium transition-colors hover:bg-muted"
          >
            <LayersIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{label}</span>
            <ChevronDown className="size-3 shrink-0 opacity-50" />
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-64 p-0 shadow-sm" align="start">
          <Command>
            <CommandInput placeholder="Find a view..." className="text-xs" />
            <CommandList>
              <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
                No matching views.
              </CommandEmpty>
              <CommandGroup heading="Built-in">
                {views.builtins.map(renderItem)}
              </CommandGroup>
              {views.personal.length > 0 && (
                <CommandGroup heading="My views">
                  {views.personal.map(renderItem)}
                </CommandGroup>
              )}
              {views.shared.length > 0 && (
                <CommandGroup heading="Shared">
                  {views.shared.map(renderItem)}
                </CommandGroup>
              )}
              <CommandSeparator />
              <CommandGroup>
                <CommandItem
                  value="save current filters as a view"
                  onSelect={() => {
                    setOpen(false)
                    setDialog({ kind: "create" })
                  }}
                  className="gap-2 text-xs"
                >
                  <PlusIcon className="size-3.5" />
                  Save current filters as a view
                </CommandItem>
                {active.viewId && (
                  <CommandItem
                    value="close view show my filters"
                    onSelect={() => choose(null)}
                    className="gap-2 pl-7 text-xs"
                  >
                    Close view
                  </CommandItem>
                )}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      {active.isError && (
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <AlertTriangleIcon className="size-3.5" />
          View not found
        </span>
      )}

      {view && isModified && (
        <>
          <span className="rounded-full border px-1.5 py-px text-[10px] font-medium leading-none text-muted-foreground">
            Modified
          </span>
          {view.can_edit && (
            <Button
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              disabled={isSaving}
              onClick={() => void controls.save()}
            >
              Save
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            disabled={isSaving}
            onClick={() => setDialog({ kind: "duplicate", view })}
          >
            Save as new
          </Button>
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-xs"
            onClick={controls.reset}
          >
            Reset
          </Button>
        </>
      )}

      {view?.kind === "custom" && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              aria-label="View actions"
              className="flex size-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted"
            >
              <MoreHorizontalIcon className="size-3.5" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="text-xs">
            <DropdownMenuItem onSelect={() => togglePin(view)}>
              {view.pinned ? (
                <PinOffIcon className="mr-2 size-3.5" />
              ) : (
                <PinIcon className="mr-2 size-3.5" />
              )}
              {view.pinned ? "Unpin from sidebar" : "Pin to sidebar"}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => setDialog({ kind: "duplicate", view })}
            >
              Duplicate
            </DropdownMenuItem>
            {view.can_edit && (
              <>
                <DropdownMenuItem
                  onSelect={() => setDialog({ kind: "rename", view })}
                >
                  Rename
                </DropdownMenuItem>
                {canShare && view.visibility === "personal" && (
                  <DropdownMenuItem
                    onSelect={() =>
                      updateView.mutate({
                        viewId: view.id,
                        requestBody: { visibility: "workspace" },
                      })
                    }
                  >
                    Share with workspace
                  </DropdownMenuItem>
                )}
                {view.visibility === "workspace" &&
                  view.owner_id === user?.id && (
                    <DropdownMenuItem
                      onSelect={() =>
                        updateView.mutate({
                          viewId: view.id,
                          requestBody: { visibility: "personal" },
                        })
                      }
                    >
                      Make personal
                    </DropdownMenuItem>
                  )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-destructive"
                  onSelect={() => setPendingDelete(view)}
                >
                  Delete
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {dialog && (
        <CaseViewSaveDialog
          mode={dialog}
          canShare={canShare}
          isPending={isSaving || updateView.isPending}
          onOpenChange={(next) => {
            if (!next) setDialog(null)
          }}
          onSubmit={async (name, visibility) => {
            if (dialog.kind === "rename") {
              await updateView.mutateAsync({
                viewId: dialog.view.id,
                requestBody: { name },
              })
            } else {
              await controls.saveAsNew(name, visibility)
            }
            setDialog(null)
          }}
        />
      )}

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDelete(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete view</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete?.visibility === "workspace"
                ? `Delete "${pendingDelete?.name}" for everyone in the workspace? Cases are not affected.`
                : `Delete "${pendingDelete?.name}"? Cases are not affected.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (!pendingDelete) return
                await deleteView.mutateAsync(pendingDelete.id)
                if (active.viewId === pendingDelete.id) {
                  active.selectView(null)
                }
                setPendingDelete(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function CaseViewSaveDialog({
  mode,
  canShare,
  isPending,
  onOpenChange,
  onSubmit,
}: {
  mode: SaveDialogMode
  canShare: boolean
  isPending: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (name: string, visibility: CaseViewVisibility) => Promise<void>
}) {
  const [name, setName] = useState(() => {
    switch (mode.kind) {
      case "rename":
        return mode.view.name
      case "duplicate":
        return `${mode.view.name} (copy)`
      default:
        return ""
    }
  })
  const [shared, setShared] = useState(false)
  const isRename = mode.kind === "rename"

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    await onSubmit(trimmed, shared ? "workspace" : "personal")
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>{isRename ? "Rename view" : "Save view"}</DialogTitle>
          <DialogDescription>
            {isRename
              ? "Give this view a new name."
              : "Save the current filters, sort, and columns."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form
            id={SAVE_VIEW_FORM_ID}
            onSubmit={handleSubmit}
            className="space-y-4"
          >
            <div className="space-y-1.5">
              <Label htmlFor="case-view-name">Name</Label>
              <Input
                id="case-view-name"
                autoFocus
                maxLength={255}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Open phishing, last 30 days"
              />
            </div>
            {!isRename && (
              <ShareToggle
                canShare={canShare}
                shared={shared}
                onSharedChange={setShared}
              />
            )}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            type="submit"
            form={SAVE_VIEW_FORM_ID}
            disabled={isPending || !name.trim()}
          >
            {isRename ? "Rename" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ShareToggle({
  canShare,
  shared,
  onSharedChange,
}: {
  canShare: boolean
  shared: boolean
  onSharedChange: (shared: boolean) => void
}) {
  const toggle = (
    <label
      className={cn(
        "flex items-center justify-between gap-2 text-sm",
        !canShare && "cursor-not-allowed opacity-50"
      )}
    >
      <span>Share with workspace</span>
      <Switch
        checked={shared}
        disabled={!canShare}
        onCheckedChange={onSharedChange}
      />
    </label>
  )
  if (canShare) return toggle
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div>{toggle}</div>
      </TooltipTrigger>
      <TooltipContent>Sharing needs permission to update cases.</TooltipContent>
    </Tooltip>
  )
}

/** Read-only chips for view conditions the filter bar cannot show. */
export function CaseViewChips({ controls }: { controls: CaseViewControls }) {
  const { view, mapped } = controls.active
  const staleCount = view?.stale_refs?.length ?? 0
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
            {view?.stale_refs?.map((ref) => ref.value).join(", ")}, which no
            longer exist. A filter left with nothing to match shows no cases.
            Update the filters and save the view to fix it.
          </TooltipContent>
        </Tooltip>
      )}
    </>
  )
}
