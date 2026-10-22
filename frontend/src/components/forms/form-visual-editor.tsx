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
import {
  ChevronRightIcon,
  GripVerticalIcon,
  HeadingIcon,
  ImageIcon,
  InfoIcon,
  type LucideIcon,
  MinusIcon,
  PlusIcon,
  TextCursorInputIcon,
  TextIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react"
import {
  type ChangeEvent,
  type ReactNode,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import type { FormNoticeTone, FormWidget } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import {
  type FormBlockKind,
  type FormConfigPath,
  type FormLayoutItem,
  formBlockKind,
  insertFormConfigItem,
  isFormLayoutItem,
  moveFormConfigItem,
  readFormVisualConfig,
  removeFormConfigItem,
  setFormConfigValue,
} from "@/lib/forms/form-config-document"
import {
  FORM_IMAGE_TYPES,
  FormImageError,
  prepareFormImage,
} from "@/lib/forms/image-data-uri"
import type { FormConfigProblem } from "@/lib/forms/yaml-diagnostics"
import { cn } from "@/lib/utils"

/** Rewrite the config text. Every visual edit goes through this. */
type EditText = (change: (text: string) => string) => void

const DEFAULT_WIDGET = "__default"

const WIDGET_LABELS: Record<FormWidget, string> = {
  text: "Text",
  long_text: "Long text",
  number: "Number",
  toggle: "Toggle",
  select: "Select",
  multi_select: "Multi-select",
  cards: "Cards",
  tags: "Tags",
  date: "Date",
  datetime: "Date and time",
  user: "User",
  file: "File",
}

const OPTION_WIDGETS: string[] = ["select", "multi_select", "cards"]

const NOTICE_TONES: FormNoticeTone[] = ["info", "success", "warning", "danger"]

/** Keys of a field entry the visual editor leaves to YAML. */
const YAML_ONLY_FIELD_KEYS = ["visible_if", "autofill", "accept", "max_files"]

const BLOCK_ICONS: Record<FormBlockKind, LucideIcon> = {
  field: TextCursorInputIcon,
  heading: HeadingIcon,
  text: TextIcon,
  notice: InfoIcon,
  divider: MinusIcon,
}

const NEW_BLOCKS: {
  kind: FormBlockKind
  label: string
  detail: string
  value: FormLayoutItem
}[] = [
  {
    kind: "heading",
    label: "Heading",
    detail: "Section title",
    value: { heading: "Section title" },
  },
  {
    kind: "text",
    label: "Text",
    detail: "Markdown",
    value: { text: "Write some text" },
  },
  {
    kind: "notice",
    label: "Notice",
    detail: "Callout box",
    value: { notice: "Something to know", tone: "info" },
  },
  {
    kind: "divider",
    label: "Divider",
    detail: "Horizontal rule",
    value: { divider: true },
  },
]

function asText(value: unknown): string {
  return typeof value === "string" ? value : ""
}

function blockTitle(item: FormLayoutItem, kind: FormBlockKind | null): string {
  switch (kind) {
    case "field":
      return asText(item.label) || asText(item.field) || "Field"
    case "heading":
      return asText(item.heading) || "Heading"
    case "text":
      return asText(item.text).split("\n")[0] || "Text"
    case "notice":
      return asText(item.notice) || "Notice"
    case "divider":
      return "Divider"
    default:
      return "Unknown block"
  }
}

const blockId = (index: number) => `block-${index}`

/**
 * A visual editor for a form config: the form's settings, then its layout
 * as a list of blocks to add, reorder, edit and remove. It reads and writes
 * the same YAML text as the code editor, so comments and keys it doesn't
 * show are kept.
 */
export function FormVisualEditor({
  text,
  onChange,
  fields,
  defaultTitle,
  readOnly,
  onEditYaml,
  problems = [],
}: {
  text: string
  onChange: (text: string) => void
  /** Workflow inputs a field block can name. */
  fields: string[]
  /** What the title falls back to when unset. */
  defaultTitle?: string
  readOnly: boolean
  /** Switch to the YAML editor, for text this editor can't show. */
  onEditYaml: () => void
  /** Problems found in the config, listed above the settings. */
  problems?: FormConfigProblem[]
}) {
  const config = useMemo(() => readFormVisualConfig(text), [text])
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )

  if (!config) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm font-medium">The YAML has errors</p>
        <p className="text-xs text-muted-foreground">
          Fix them in YAML to edit this form visually.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-1 h-7 text-xs"
          onClick={onEditYaml}
        >
          Edit YAML
        </Button>
      </div>
    )
  }

  const edit: EditText = (change) => onChange(change(text))
  const layout = config.layout
  const usedFields = new Set(
    layout.map((item) => asText(item.field)).filter(Boolean)
  )

  function addBlock(value: FormLayoutItem) {
    edit((current) => insertFormConfigItem(current, ["layout"], value))
    setOpenIndex(layout.length)
  }

  function handleDragEnd({ active, over }: DragEndEvent) {
    if (!over || active.id === over.id) {
      return
    }
    const ids = layout.map((_, index) => blockId(index))
    const from = ids.indexOf(String(active.id))
    const to = ids.indexOf(String(over.id))
    edit((current) => moveFormConfigItem(current, ["layout"], from, to))
    setOpenIndex(openIndex === from ? to : null)
  }

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 p-4">
        {problems.length > 0 && (
          <ul
            aria-label="Problems"
            className="flex flex-col gap-1 rounded-md border px-3 py-2 text-xs"
          >
            {problems.map((problem) => (
              <li
                key={`${problem.from}-${problem.message}`}
                className="flex items-start gap-2"
              >
                <span
                  className={cn(
                    "mt-1.5 size-1.5 flex-none rounded-full",
                    problem.severity === "error" ? "bg-red-500" : "bg-amber-500"
                  )}
                />
                <span>{problem.message}</span>
              </li>
            ))}
          </ul>
        )}
        <fieldset disabled={readOnly} className="flex flex-col gap-3">
          <TextSetting
            label="Title"
            value={config.title}
            placeholder={defaultTitle}
            onChange={(value) =>
              edit((current) => setFormConfigValue(current, ["title"], value))
            }
          />
          <TextSetting
            label="Description"
            multiline
            value={config.description}
            placeholder="Defaults to the workflow's description"
            onChange={(value) =>
              edit((current) =>
                setFormConfigValue(current, ["description"], value)
              )
            }
          />
          <TextSetting
            label="Success message"
            multiline
            value={config.successMessage}
            placeholder="Shown after a submission"
            onChange={(value) =>
              edit((current) =>
                setFormConfigValue(current, ["success_message"], value)
              )
            }
          />
        </fieldset>

        <section className="flex flex-col gap-2" aria-label="Blocks">
          <div className="flex h-7 items-center justify-between">
            <h3 className="text-xs font-medium">Blocks</h3>
            {!readOnly && (
              <AddBlockMenu
                fields={fields}
                usedFields={usedFields}
                onAdd={addBlock}
              />
            )}
          </div>
          {layout.length === 0 ? (
            <p className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
              No blocks yet. Add one to lay out the form.
            </p>
          ) : (
            <DndContext
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext
                items={layout.map((_, index) => blockId(index))}
                strategy={verticalListSortingStrategy}
              >
                <div className="flex flex-col divide-y rounded-md border">
                  {layout.map((item, index) => {
                    const path: FormConfigPath = ["layout", index]
                    const isOpen = openIndex === index
                    return (
                      <BlockRow
                        // Blocks have no identity besides their position.
                        key={blockId(index)}
                        id={blockId(index)}
                        item={item}
                        open={isOpen}
                        readOnly={readOnly}
                        onToggle={() => setOpenIndex(isOpen ? null : index)}
                        onRemove={() => {
                          edit((current) =>
                            removeFormConfigItem(current, ["layout"], index)
                          )
                          setOpenIndex(null)
                        }}
                      >
                        <BlockEditor
                          item={item}
                          path={path}
                          fields={fields}
                          edit={edit}
                        />
                      </BlockRow>
                    )
                  })}
                </div>
              </SortableContext>
            </DndContext>
          )}
        </section>
      </div>
    </div>
  )
}

function AddBlockMenu({
  fields,
  usedFields,
  onAdd,
}: {
  fields: string[]
  usedFields: Set<string>
  onAdd: (value: FormLayoutItem) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 gap-1 px-2 text-xs">
          <PlusIcon className="size-3.5" />
          Add block
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuSub>
          <DropdownMenuSubTrigger className="text-xs">
            <TextCursorInputIcon className="mr-2 size-3.5" />
            Field
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent className="max-h-72 overflow-auto">
            {fields.length === 0 ? (
              <DropdownMenuItem disabled className="text-xs">
                No workflow inputs
              </DropdownMenuItem>
            ) : (
              fields.map((name) => (
                <DropdownMenuItem
                  key={name}
                  className="gap-3 text-xs"
                  onSelect={() => onAdd({ field: name })}
                >
                  <span className="font-mono">{name}</span>
                  {usedFields.has(name) && (
                    <span className="ml-auto text-muted-foreground">
                      In use
                    </span>
                  )}
                </DropdownMenuItem>
              ))
            )}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        {NEW_BLOCKS.map(({ kind, label, detail, value }) => {
          const Icon = BLOCK_ICONS[kind]
          return (
            <DropdownMenuItem
              key={kind}
              className="text-xs"
              onSelect={() => onAdd(value)}
            >
              <Icon className="mr-2 size-3.5" />
              {label}
              <span className="ml-auto text-muted-foreground">{detail}</span>
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function BlockRow({
  id,
  item,
  open,
  readOnly,
  onToggle,
  onRemove,
  children,
}: {
  id: string
  item: FormLayoutItem
  open: boolean
  readOnly: boolean
  onToggle: () => void
  onRemove: () => void
  children: ReactNode
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled: readOnly })
  const kind = formBlockKind(item)
  const Icon = kind ? BLOCK_ICONS[kind] : TextIcon

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("bg-background", isDragging && "relative z-10 opacity-80")}
    >
      <div className="flex h-9 items-center gap-1.5 px-2">
        {!readOnly && (
          <button
            type="button"
            aria-label="Drag to reorder"
            className="flex-none cursor-grab touch-none text-muted-foreground hover:text-foreground"
            {...attributes}
            {...listeners}
          >
            <GripVerticalIcon className="size-3.5" />
          </button>
        )}
        <button
          type="button"
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs"
          onClick={onToggle}
        >
          <ChevronRightIcon
            className={cn(
              "size-3.5 flex-none text-muted-foreground transition-transform",
              open && "rotate-90"
            )}
          />
          <Icon className="size-3.5 flex-none text-muted-foreground" />
          <span className="truncate font-medium">{blockTitle(item, kind)}</span>
          {kind === "field" && asText(item.label) && (
            <span className="truncate font-mono text-[11px] text-muted-foreground">
              {asText(item.field)}
            </span>
          )}
          {item.hidden === true && (
            <span className="flex-none text-[11px] text-muted-foreground">
              Hidden
            </span>
          )}
        </button>
        {!readOnly && (
          <Button
            variant="ghost"
            size="icon"
            className="size-6 flex-none text-muted-foreground"
            aria-label="Remove block"
            onClick={onRemove}
          >
            <Trash2Icon className="size-3.5" />
          </Button>
        )}
      </div>
      {open && (
        <fieldset
          disabled={readOnly}
          className="flex flex-col gap-3 border-t px-3 py-3 sm:pl-9"
        >
          {children}
        </fieldset>
      )}
    </div>
  )
}

function BlockEditor({
  item,
  path,
  fields,
  edit,
}: {
  item: FormLayoutItem
  path: FormConfigPath
  fields: string[]
  edit: EditText
}) {
  function set(key: string, value: unknown, keepEmpty = false) {
    edit((current) =>
      setFormConfigValue(current, [...path, key], value, { keepEmpty })
    )
  }

  switch (formBlockKind(item)) {
    case "field":
      return (
        <FieldBlockEditor item={item} path={path} fields={fields} edit={edit} />
      )
    case "heading":
      return (
        <TextSetting
          label="Heading"
          value={asText(item.heading)}
          onChange={(value) => set("heading", value, true)}
        />
      )
    case "text":
      return (
        <TextSetting
          label="Text"
          hint="Markdown"
          multiline
          value={asText(item.text)}
          onChange={(value) => set("text", value, true)}
        />
      )
    case "notice":
      return (
        <>
          <TextSetting
            label="Notice"
            multiline
            value={asText(item.notice)}
            onChange={(value) => set("notice", value, true)}
          />
          <Setting label="Tone">
            {(id) => (
              <Select
                value={asText(item.tone) || "info"}
                onValueChange={(value) => set("tone", value)}
              >
                <SelectTrigger id={id} className="h-8 w-40 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {NOTICE_TONES.map((tone) => (
                    <SelectItem key={tone} value={tone} className="text-xs">
                      {tone.charAt(0).toUpperCase() + tone.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </Setting>
        </>
      )
    case "divider":
      return (
        <p className="text-xs text-muted-foreground">
          A horizontal rule. Nothing to set.
        </p>
      )
    default:
      return (
        <p className="text-xs text-muted-foreground">
          Edit this block in YAML.
        </p>
      )
  }
}

function FieldBlockEditor({
  item,
  path,
  fields,
  edit,
}: {
  item: FormLayoutItem
  path: FormConfigPath
  fields: string[]
  edit: EditText
}) {
  const name = asText(item.field)
  const widget = asText(item.widget)
  const names = !name || fields.includes(name) ? fields : [name, ...fields]
  const yamlOnly = YAML_ONLY_FIELD_KEYS.filter((key) => key in item)

  function set(key: string, value: unknown) {
    edit((current) => setFormConfigValue(current, [...path, key], value))
  }

  return (
    <>
      <Setting label="Input">
        {(id) => (
          <Select value={name} onValueChange={(value) => set("field", value)}>
            <SelectTrigger id={id} className="h-8 text-xs">
              <SelectValue placeholder="Pick a workflow input" />
            </SelectTrigger>
            <SelectContent>
              {names.map((option) => (
                <SelectItem
                  key={option}
                  value={option}
                  className="font-mono text-xs"
                >
                  {option}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </Setting>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextSetting
          label="Label"
          value={asText(item.label)}
          placeholder={name}
          onChange={(value) => set("label", value)}
        />
        <TextSetting
          label="Placeholder"
          value={asText(item.placeholder)}
          onChange={(value) => set("placeholder", value)}
        />
      </div>
      <TextSetting
        label="Help"
        value={asText(item.help)}
        onChange={(value) => set("help", value)}
      />
      <div className="grid items-end gap-3 sm:grid-cols-2">
        <Setting label="Widget">
          {(id) => (
            <Select
              value={widget || DEFAULT_WIDGET}
              onValueChange={(value) =>
                set("widget", value === DEFAULT_WIDGET ? "" : value)
              }
            >
              <SelectTrigger id={id} className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_WIDGET} className="text-xs">
                  Default for the input
                </SelectItem>
                {Object.entries(WIDGET_LABELS).map(([value, label]) => (
                  <SelectItem key={value} value={value} className="text-xs">
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Setting>
        <SwitchSetting
          label="Hidden"
          checked={item.hidden === true}
          onChange={(checked) => set("hidden", checked)}
        />
      </div>
      {OPTION_WIDGETS.includes(widget) && (
        <OptionsEditor
          options={item.options}
          path={[...path, "options"]}
          withImages={widget === "cards"}
          edit={edit}
        />
      )}
      {yamlOnly.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          Also set in YAML: {yamlOnly.join(", ")}
        </p>
      )}
    </>
  )
}

function OptionsEditor({
  options,
  path,
  withImages,
  edit,
}: {
  options: unknown
  path: FormConfigPath
  withImages: boolean
  edit: EditText
}) {
  const list = Array.isArray(options) ? options : []

  function set(index: number, key: string, value: unknown, keepEmpty = false) {
    edit((current) =>
      setFormConfigValue(current, [...path, index, key], value, { keepEmpty })
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">Options</span>
        <span className="text-[11px] text-muted-foreground">
          Leave empty to use the input's choices
        </span>
      </div>
      {list.map((raw, index) => {
        const option = isFormLayoutItem(raw) ? raw : {}
        return (
          <div
            // Options have no identity besides their position.
            key={`option-${index}`}
            className="flex items-start gap-2"
          >
            {withImages && (
              <OptionImagePicker
                image={asText(option.image)}
                onChange={(value) => set(index, "image", value)}
              />
            )}
            <div className="grid min-w-0 flex-1 gap-2 sm:grid-cols-2">
              <Input
                aria-label="Option value"
                placeholder="Value"
                className="h-8 font-mono text-xs"
                value={asText(option.value)}
                onChange={(event) =>
                  set(index, "value", event.target.value, true)
                }
              />
              <Input
                aria-label="Option label"
                placeholder="Label"
                className="h-8 text-xs"
                value={asText(option.label)}
                onChange={(event) => set(index, "label", event.target.value)}
              />
              {withImages && (
                <Input
                  aria-label="Option description"
                  placeholder="Description"
                  className="h-8 text-xs sm:col-span-2"
                  value={asText(option.description)}
                  onChange={(event) =>
                    set(index, "description", event.target.value)
                  }
                />
              )}
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="size-8 flex-none text-muted-foreground"
              aria-label="Remove option"
              onClick={() =>
                edit((current) =>
                  removeFormConfigItem(current, path, index, {
                    dropEmpty: true,
                  })
                )
              }
            >
              <XIcon className="size-3.5" />
            </Button>
          </div>
        )
      })}
      <Button
        variant="outline"
        size="sm"
        className="h-7 w-fit gap-1 px-2 text-xs"
        onClick={() =>
          edit((current) =>
            insertFormConfigItem(current, path, {
              value: `option_${list.length + 1}`,
            })
          )
        }
      >
        <PlusIcon className="size-3.5" />
        Add option
      </Button>
    </div>
  )
}

/**
 * Pick a PNG, JPEG or WebP for a card, shrunk in the browser and stored in
 * the config as a base64 `data:` URI. Nothing is uploaded.
 */
function OptionImagePicker({
  image,
  onChange,
}: {
  image: string
  onChange: (image: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isWorking, setIsWorking] = useState(false)

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Clear the input so picking the same file again fires a change.
    event.target.value = ""
    if (!file) {
      return
    }
    setIsWorking(true)
    try {
      onChange(await prepareFormImage(file))
    } catch (error) {
      toast({
        title: "Couldn't add the image",
        description:
          error instanceof FormImageError
            ? error.message
            : "Something went wrong. Try another image.",
        variant: "destructive",
      })
    } finally {
      setIsWorking(false)
    }
  }

  return (
    <div className="relative size-8 flex-none">
      <input
        ref={inputRef}
        type="file"
        accept={FORM_IMAGE_TYPES.join(",")}
        className="hidden"
        aria-label="Option image file"
        onChange={handleFile}
      />
      {image ? (
        <>
          <button
            type="button"
            aria-label="Replace image"
            className="size-8 overflow-hidden rounded-md border"
            onClick={() => inputRef.current?.click()}
          >
            {/* An inline data URI; see the cards widget. */}
            <img src={image} alt="" className="size-full object-cover" />
          </button>
          <button
            type="button"
            aria-label="Remove image"
            className="absolute -right-1.5 -top-1.5 rounded-full border bg-background p-0.5 text-muted-foreground hover:text-foreground"
            onClick={() => onChange("")}
          >
            <XIcon className="size-2.5" />
          </button>
        </>
      ) : (
        <Button
          variant="outline"
          size="icon"
          className="size-8 text-muted-foreground"
          aria-label="Add image"
          disabled={isWorking}
          onClick={() => inputRef.current?.click()}
        >
          <ImageIcon className="size-3.5" />
        </Button>
      )}
    </div>
  )
}

function Setting({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: (id: string) => ReactNode
}) {
  const id = useId()
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id} className="text-xs">
          {label}
        </Label>
        {hint && (
          <span className="text-[11px] text-muted-foreground">{hint}</span>
        )}
      </div>
      {children(id)}
    </div>
  )
}

function TextSetting({
  label,
  hint,
  value,
  placeholder,
  multiline = false,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  multiline?: boolean
  onChange: (value: string) => void
}) {
  return (
    <Setting label={label} hint={hint}>
      {(id) =>
        multiline ? (
          <Textarea
            id={id}
            rows={3}
            className="min-h-0 text-xs"
            value={value}
            placeholder={placeholder}
            onChange={(event) => onChange(event.target.value)}
          />
        ) : (
          <Input
            id={id}
            className="h-8 text-xs"
            value={value}
            placeholder={placeholder}
            onChange={(event) => onChange(event.target.value)}
          />
        )
      }
    </Setting>
  )
}

function SwitchSetting({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const id = useId()
  return (
    <div className="flex h-8 items-center gap-2">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
    </div>
  )
}
