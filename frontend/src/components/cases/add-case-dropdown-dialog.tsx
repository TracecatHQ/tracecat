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
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { zodResolver } from "@hookform/resolvers/zod"
import { GripVerticalIcon, PlusIcon, Trash2Icon } from "lucide-react"
import { useCallback, useId, useState } from "react"
import { useForm } from "react-hook-form"
import z from "zod"
import { ColorPicker } from "@/components/color-picker"
import { IconPicker } from "@/components/form/icon-picker"
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
import { useCaseDropdownDefinitions } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s_-]/g, "")
    .replace(/[\s]+/g, "_")
    .replace(/[_-]+/g, "_")
    .slice(0, 50)
}

const createDropdownSchema = z.object({
  name: z
    .string()
    .min(1, "Name cannot be empty")
    .max(50, "Name cannot be longer than 50 characters")
    .trim(),
  icon_name: z.string().max(100).optional(),
  is_ordered: z.boolean(),
})

type CreateDropdownFormValues = z.infer<typeof createDropdownSchema>

export interface OptionInput {
  id: string
  label: string
  icon_name: string
  color: string
}

export function createEmptyOption(id: string): OptionInput {
  return {
    id,
    label: "",
    icon_name: "",
    color: "",
  }
}

export interface SortableOptionRowProps {
  opt: OptionInput
  index: number
  isOrdered: boolean
  canRemove: boolean
  onOptionChange: (
    index: number,
    field: keyof OptionInput,
    value: string
  ) => void
  onRemove: (index: number) => void
}

export function SortableOptionRow({
  opt,
  index,
  isOrdered,
  canRemove,
  onOptionChange,
  onRemove,
}: SortableOptionRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: opt.id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div ref={setNodeRef} style={style} className="flex items-center gap-2">
      <button
        type="button"
        className="shrink-0 cursor-grab touch-none text-muted-foreground hover:text-foreground"
        {...attributes}
        {...listeners}
      >
        <GripVerticalIcon className="size-4" />
      </button>
      {isOrdered && (
        <span className="shrink-0 text-xs font-mono text-muted-foreground w-6 text-center">
          #{index}
        </span>
      )}
      <Input
        className="text-sm"
        placeholder="Label"
        value={opt.label}
        onChange={(e) => onOptionChange(index, "label", e.target.value)}
      />
      <IconPicker
        className="size-9 shrink-0"
        placeholder="Icon"
        value={opt.icon_name || undefined}
        onValueChange={(val) => onOptionChange(index, "icon_name", val)}
      />
      <ColorPicker
        value={opt.color || "#aabbcc"}
        onChange={(val) => onOptionChange(index, "color", val)}
      />
      {canRemove && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="size-8 shrink-0 p-0"
          onClick={() => onRemove(index)}
        >
          <Trash2Icon className="size-3.5 text-muted-foreground" />
        </Button>
      )}
    </div>
  )
}

interface AddCaseDropdownDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCaseDropdownDialog({
  open,
  onOpenChange,
}: AddCaseDropdownDialogProps) {
  const workspaceId = useWorkspaceId()
  const { dropdownDefinitions, createDropdownDefinition } =
    useCaseDropdownDefinitions(workspaceId)

  const idPrefix = useId()
  const [nextOptionId, setNextOptionId] = useState(1)

  const makeOptionId = useCallback(
    (n: number) => `${idPrefix}-opt-${n}`,
    [idPrefix]
  )

  const [options, setOptions] = useState<OptionInput[]>([
    createEmptyOption(makeOptionId(0)),
  ])

  const methods = useForm<CreateDropdownFormValues>({
    resolver: zodResolver(createDropdownSchema),
    defaultValues: {
      name: "",
      icon_name: "",
      is_ordered: false,
    },
  })

  const isOrdered = methods.watch("is_ordered")

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  const handleAddOption = () => {
    const id = makeOptionId(nextOptionId)
    setNextOptionId((n) => n + 1)
    setOptions([...options, createEmptyOption(id)])
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

  const handleCreate = async (formValues: CreateDropdownFormValues) => {
    try {
      const nameExists = dropdownDefinitions?.some(
        (d) => d.name.toLowerCase() === formValues.name.trim().toLowerCase()
      )
      if (nameExists) {
        methods.setError("name", {
          type: "manual",
          message: "A dropdown with this name already exists",
        })
        return
      }

      const dropdownRef = slugify(formValues.name)
      if (!dropdownRef) {
        methods.setError("name", {
          type: "manual",
          message:
            "Name must produce a valid reference (use alphanumeric characters)",
        })
        return
      }

      const validOptions = options
        .filter((o) => o.label.trim())
        .map((o, i) => ({
          label: o.label.trim(),
          ref: slugify(o.label),
          icon_name: o.icon_name.trim() || undefined,
          color: o.color.trim() || undefined,
          position: i,
        }))
        .filter((o) => o.ref)

      if (validOptions.length === 0) {
        methods.setError("name", {
          type: "manual",
          message:
            "At least one option must have a label that produces a valid reference (use alphanumeric characters)",
        })
        return
      }

      await createDropdownDefinition({
        workspaceId,
        requestBody: {
          name: formValues.name.trim(),
          ref: dropdownRef,
          icon_name: formValues.icon_name?.trim() || undefined,
          is_ordered: formValues.is_ordered,
          options: validOptions,
        },
      })
      methods.reset()
      setOptions([createEmptyOption(makeOptionId(nextOptionId))])
      setNextOptionId((n) => n + 1)
      onOpenChange(false)
    } catch (error) {
      console.error("Error creating dropdown", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Create new dropdown</DialogTitle>
          <DialogDescription>
            Define a custom dropdown with options for your cases.
          </DialogDescription>
        </DialogHeader>
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(handleCreate)}>
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
                          placeholder="e.g. Determination"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        Display name for the dropdown
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
                        Options will be ranked by their position from top to
                        bottom.
                      </FormDescription>
                    </div>
                    <FormControl>
                      <Switch
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                  </FormItem>
                )}
              />
              <div className="space-y-2">
                <FormLabel className="text-sm">Options</FormLabel>
                <FormDescription>
                  Add options for this dropdown. Each needs a label.
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
                <Button className="ml-auto space-x-2" type="submit">
                  Create dropdown
                </Button>
              </DialogFooter>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
