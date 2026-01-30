"use client"

import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core"
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  CopyIcon,
  ListIcon,
  PencilIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react"
import { useCallback, useEffect, useId, useState } from "react"
import { useForm } from "react-hook-form"
import z from "zod"
import type {
  CaseDropdownDefinitionRead,
  CaseDropdownDefinitionUpdate,
  CaseDropdownOptionRead,
} from "@/client"
import {
  createEmptyOption,
  type OptionInput,
  SortableOptionRow,
  slugify,
} from "@/components/cases/add-case-dropdown-dialog"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { IconPicker } from "@/components/form/icon-picker"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { resolveLucideIcon } from "@/lib/lucide-icon-resolver"

const updateDefinitionSchema = z.object({
  name: z
    .string()
    .min(1, "Name cannot be empty")
    .max(50, "Name cannot be longer than 50 characters")
    .trim()
    .optional(),
  icon_name: z.string().max(100).optional(),
  is_ordered: z.boolean().optional(),
})

enum DefinitionAction {
  Edit = "edit",
  Delete = "delete",
}

interface DropdownsTableProps {
  definitions: CaseDropdownDefinitionRead[]
  onDeleteDefinition: (definitionId: string) => Promise<void>
  onUpdateDefinition: (
    definitionId: string,
    params: CaseDropdownDefinitionUpdate
  ) => Promise<void>
  onAddOption?: (
    definitionId: string,
    option: {
      label: string
      ref: string
      icon_name?: string
      color?: string
      position: number
    }
  ) => Promise<void>
  onUpdateOption?: (
    definitionId: string,
    optionId: string,
    option: {
      label?: string
      ref?: string
      icon_name?: string
      color?: string
      position?: number
    }
  ) => Promise<void>
  onDeleteOption?: (definitionId: string, optionId: string) => Promise<void>
  onReorderOptions?: (
    definitionId: string,
    optionIds: string[]
  ) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
}

export function DropdownsTable({
  definitions,
  onDeleteDefinition,
  onUpdateDefinition,
  onAddOption,
  onUpdateOption,
  onDeleteOption,
  onReorderOptions,
  isDeleting,
  isUpdating,
}: DropdownsTableProps) {
  const [selectedDef, setSelectedDef] =
    useState<CaseDropdownDefinitionRead | null>(null)
  const [action, setAction] = useState<DefinitionAction | null>(null)

  const handleCloseEdit = () => {
    setSelectedDef(null)
    setAction(null)
  }

  return (
    <>
      <AlertDialog
        open={action === DefinitionAction.Delete && selectedDef !== null}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedDef(null)
            setAction(null)
          }
        }}
      >
        <DataTable
          data={definitions}
          columns={[
            {
              accessorKey: "name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Dropdown name"
                />
              ),
              cell: ({ row }) => {
                const def = row.original
                const Icon = resolveLucideIcon(def.icon_name) ?? ListIcon
                return (
                  <div className="flex items-center gap-2">
                    <Icon className="size-4 text-muted-foreground" />
                    <span className="text-xs text-foreground/80">
                      {def.name}
                    </span>
                  </div>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "options",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Options"
                />
              ),
              cell: ({ row }) => {
                const options = row.original.options ?? []
                return (
                  <div className="flex flex-wrap gap-1">
                    {options.slice(0, 5).map((opt) => (
                      <Badge
                        key={opt.id}
                        variant="outline"
                        className="text-[10px]"
                      >
                        {opt.color && (
                          <div
                            className="mr-1 size-2 shrink-0 rounded-full"
                            style={{ backgroundColor: opt.color }}
                          />
                        )}
                        {opt.label}
                      </Badge>
                    ))}
                    {options.length > 5 && (
                      <span className="text-[10px] text-muted-foreground">
                        +{options.length - 5}
                      </span>
                    )}
                  </div>
                )
              },
              enableSorting: false,
              enableHiding: false,
            },
            {
              accessorKey: "ref",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Reference"
                />
              ),
              cell: ({ row }) => (
                <span className="font-mono text-xs text-muted-foreground">
                  {row.original.ref}
                </span>
              ),
              enableSorting: false,
              enableHiding: false,
            },
            {
              accessorKey: "is_ordered",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Ordered"
                />
              ),
              cell: ({ row }) => (
                <span className="text-xs text-muted-foreground">
                  {row.original.is_ordered ? "Yes" : "No"}
                </span>
              ),
              enableSorting: false,
              enableHiding: false,
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                return (
                  <div className="flex justify-end">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="size-8 p-0">
                          <span className="sr-only">Open menu</span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(row.original.ref)
                          }
                        >
                          <CopyIcon className="mr-2 size-3" />
                          Copy reference
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => {
                            setSelectedDef(row.original)
                            setAction(DefinitionAction.Edit)
                          }}
                        >
                          <PencilIcon className="mr-2 size-3" />
                          Edit dropdown
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedDef(row.original)
                            setAction(DefinitionAction.Delete)
                          }}
                        >
                          <Trash2Icon className="mr-2 size-3" />
                          Delete dropdown
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
        {action === DefinitionAction.Delete && selectedDef && (
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete dropdown</AlertDialogTitle>
              <AlertDialogDescription>
                Are you sure you want to delete the dropdown{" "}
                <strong>{selectedDef.name}</strong>? This action cannot be
                undone and will remove all dropdown values from cases.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={isDeleting}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={async () => {
                  if (selectedDef) {
                    try {
                      await onDeleteDefinition(selectedDef.id)
                      setSelectedDef(null)
                      setAction(null)
                    } catch (error) {
                      console.error("Failed to delete dropdown:", error)
                    }
                  }
                }}
                disabled={isDeleting}
              >
                <Trash2Icon className="mr-2 size-4" />
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
      {action === DefinitionAction.Edit && selectedDef && (
        <EditDefinitionDialog
          definition={selectedDef}
          onUpdate={onUpdateDefinition}
          onAddOption={onAddOption}
          onUpdateOption={onUpdateOption}
          onDeleteOption={onDeleteOption}
          onReorderOptions={onReorderOptions}
          isUpdating={isUpdating}
          onClose={handleCloseEdit}
        />
      )}
    </>
  )
}

/** Convert existing API options to local OptionInput shape */
function toOptionInput(
  opt: CaseDropdownOptionRead,
  localId: string
): OptionInput {
  return {
    id: localId,
    label: opt.label,
    icon_name: opt.icon_name ?? "",
    color: opt.color ?? "",
  }
}

interface EditOptionState extends OptionInput {
  /** Original API option ID, null for newly added options */
  apiId: string | null
}

function EditDefinitionDialog({
  definition,
  onUpdate,
  onAddOption,
  onUpdateOption,
  onDeleteOption,
  onReorderOptions,
  isUpdating,
  onClose,
}: {
  definition: CaseDropdownDefinitionRead
  onUpdate: (
    definitionId: string,
    params: CaseDropdownDefinitionUpdate
  ) => Promise<void>
  onAddOption?: (
    definitionId: string,
    option: {
      label: string
      ref: string
      icon_name?: string
      color?: string
      position: number
    }
  ) => Promise<void>
  onUpdateOption?: (
    definitionId: string,
    optionId: string,
    option: {
      label?: string
      ref?: string
      icon_name?: string
      color?: string
      position?: number
    }
  ) => Promise<void>
  onDeleteOption?: (definitionId: string, optionId: string) => Promise<void>
  onReorderOptions?: (
    definitionId: string,
    optionIds: string[]
  ) => Promise<void>
  isUpdating?: boolean
  onClose: () => void
}) {
  const idPrefix = useId()
  const [nextOptionId, setNextOptionId] = useState(0)

  const makeOptionId = useCallback(
    (n: number) => `${idPrefix}-edit-opt-${n}`,
    [idPrefix]
  )

  const [options, setOptions] = useState<EditOptionState[]>(() => {
    const existing = (definition.options ?? []).map((opt, i) => ({
      ...toOptionInput(opt, makeOptionId(i)),
      apiId: opt.id,
    }))
    setNextOptionId(existing.length)
    return existing
  })

  // Track the original options for diffing
  const [originalOptions] = useState(() =>
    (definition.options ?? []).map((opt) => ({
      id: opt.id,
      label: opt.label,
      ref: opt.ref,
      icon_name: opt.icon_name ?? "",
      color: opt.color ?? "",
    }))
  )

  const [isSaving, setIsSaving] = useState(false)

  const methods = useForm<CaseDropdownDefinitionUpdate>({
    resolver: zodResolver(updateDefinitionSchema),
    defaultValues: {
      name: definition.name,
      icon_name: definition.icon_name ?? "",
      is_ordered: definition.is_ordered,
    },
  })

  useEffect(() => {
    methods.reset({
      name: definition.name,
      icon_name: definition.icon_name ?? "",
      is_ordered: definition.is_ordered,
    })
  }, [methods, definition.name, definition.icon_name, definition.is_ordered])

  const isOrdered = methods.watch("is_ordered") ?? false

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  const handleAddOption = () => {
    const id = makeOptionId(nextOptionId)
    setNextOptionId((n) => n + 1)
    setOptions([...options, { ...createEmptyOption(id), apiId: null }])
  }

  const handleRemoveOption = (index: number) => {
    setOptions(options.filter((_, i) => i !== index))
  }

  const handleOptionChange = (
    index: number,
    field: keyof OptionInput,
    value: string
  ) => {
    const updated = [...options]
    updated[index] = { ...updated[index], [field]: value }
    setOptions(updated)
  }

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return

    const oldIndex = options.findIndex((o) => o.id === active.id)
    const newIndex = options.findIndex((o) => o.id === over.id)
    if (oldIndex === -1 || newIndex === -1) return

    const updated = [...options]
    const [moved] = updated.splice(oldIndex, 1)
    updated.splice(newIndex, 0, moved)
    setOptions(updated)
  }

  const handleSave = async (formValues: CaseDropdownDefinitionUpdate) => {
    setIsSaving(true)
    try {
      // 1. Update definition fields
      await onUpdate(definition.id, {
        name: formValues.name || undefined,
        icon_name: formValues.icon_name?.trim() || undefined,
        is_ordered: formValues.is_ordered,
      })

      // 2. Delete removed options
      const currentApiIds = new Set(
        options.filter((o) => o.apiId).map((o) => o.apiId)
      )
      const deletedOptions = originalOptions.filter(
        (o) => !currentApiIds.has(o.id)
      )
      for (const opt of deletedOptions) {
        await onDeleteOption?.(definition.id, opt.id)
      }

      // 3. Add new options
      for (let i = 0; i < options.length; i++) {
        const opt = options[i]
        if (!opt.apiId && opt.label.trim()) {
          await onAddOption?.(definition.id, {
            label: opt.label.trim(),
            ref: slugify(opt.label),
            icon_name: opt.icon_name.trim() || undefined,
            color: opt.color.trim() || undefined,
            position: i,
          })
        }
      }

      // 4. Update changed existing options
      for (let i = 0; i < options.length; i++) {
        const opt = options[i]
        if (!opt.apiId) continue

        const orig = originalOptions.find((o) => o.id === opt.apiId)
        if (!orig) continue

        const changed =
          orig.label !== opt.label.trim() ||
          orig.icon_name !== opt.icon_name.trim() ||
          orig.color !== opt.color.trim()

        if (changed) {
          await onUpdateOption?.(definition.id, opt.apiId, {
            label: opt.label.trim() || undefined,
            ref: slugify(opt.label) || undefined,
            icon_name: opt.icon_name.trim() || undefined,
            color: opt.color.trim() || undefined,
          })
        }
      }

      // 5. Reorder if order changed
      const existingApiIds = options
        .filter((o) => o.apiId)
        .map((o) => o.apiId as string)
      const originalOrder = originalOptions.map((o) => o.id)
      const orderChanged =
        existingApiIds.length !== originalOrder.length ||
        existingApiIds.some((id, i) => id !== originalOrder[i])

      if (orderChanged && existingApiIds.length > 0) {
        await onReorderOptions?.(definition.id, existingApiIds)
      }

      onClose()
    } catch (error) {
      console.error("Error saving dropdown changes", error)
      methods.setError("name", {
        type: "manual",
        message: `Error saving changes: ${String(error)}`,
      })
    } finally {
      setIsSaving(false)
    }
  }

  const saving = isSaving || isUpdating

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit dropdown</DialogTitle>
          <DialogDescription>
            Edit the name, icon, settings, and options of the dropdown.
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(handleSave)}>
            <div className="space-y-4">
              <div className="flex items-start gap-3">
                <FormField
                  control={methods.control}
                  name="icon_name"
                  render={({ field }) => (
                    <FormItem className="mt-6">
                      <FormControl>
                        <IconPicker
                          className="size-9 shrink-0"
                          placeholder="Icon"
                          value={field.value || undefined}
                          onValueChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <FormField
                  control={methods.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem className="flex-1">
                      <FormLabel className="text-sm">Name</FormLabel>
                      <FormControl>
                        <Input
                          className="text-sm"
                          placeholder="Name"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormDescription>
                        Max 50 alphanumeric characters
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
              <FormField
                control={methods.control}
                name="is_ordered"
                render={({ field }) => (
                  <FormItem className="flex items-center justify-between rounded-lg border p-3">
                    <div className="space-y-0.5">
                      <FormLabel className="text-sm">Ordered</FormLabel>
                      <FormDescription>
                        Enable sorting by this dropdown in the cases list
                      </FormDescription>
                    </div>
                    <FormControl>
                      <Switch
                        checked={field.value ?? false}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <div className="space-y-2">
                <FormLabel className="text-sm">Options</FormLabel>
                <FormDescription>
                  Drag to reorder. Edit label, icon, and color per option.
                </FormDescription>
                <DndContext
                  sensors={sensors}
                  collisionDetection={closestCenter}
                  onDragEnd={handleDragEnd}
                >
                  <SortableContext
                    items={options.map((o) => o.id)}
                    strategy={verticalListSortingStrategy}
                  >
                    {options.map((opt, index) => (
                      <SortableOptionRow
                        key={opt.id}
                        opt={opt}
                        index={index}
                        isOrdered={isOrdered}
                        canRemove={options.length > 1}
                        onOptionChange={handleOptionChange}
                        onRemove={handleRemoveOption}
                      />
                    ))}
                  </SortableContext>
                </DndContext>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={handleAddOption}
                >
                  <PlusIcon className="mr-1 size-3.5" />
                  Add option
                </Button>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onClose}
                  disabled={saving}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  className="flex items-center gap-2"
                  disabled={saving}
                >
                  <PencilIcon className="size-4" />
                  Save changes
                </Button>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

const defaultToolbarProps: DataTableToolbarProps<CaseDropdownDefinitionRead> = {
  filterProps: {
    placeholder: "Filter dropdowns by name...",
    column: "name",
  },
}
