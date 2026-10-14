"use client"

import {
  CheckIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  InfoIcon,
  type LucideIcon,
  TriangleAlertIcon,
} from "lucide-react"
import Link from "next/link"
import {
  type ComponentType,
  type FormEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react"
import type {
  FormNoticeBlock,
  FormNoticeTone,
  FormPreview,
  FormSubmissionReceipt,
  FormWidget,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { FormMarkdown } from "@/components/forms/form-markdown"
import { formStyles } from "@/components/forms/form-styles"
import {
  DateWidget,
  type FormWidgetProps,
  SelectWidget,
  TagsWidget,
  TextWidget,
  ToggleWidget,
} from "@/components/forms/widgets"
import { CardsWidget } from "@/components/forms/widgets/cards"
import { FileWidget } from "@/components/forms/widgets/file"
import { UserWidget } from "@/components/forms/widgets/user"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { getFormErrorDetail } from "@/lib/forms/errors"
import type { FormFileUploader } from "@/lib/forms/files"
import {
  type FormBlock,
  type FormValues,
  getInitialFormValues,
  getVisibleFields,
  isFieldBlock,
  toSubmissionValues,
  validateFormValues,
} from "@/lib/forms/values"
import { cn } from "@/lib/utils"

/** Where a requester's own submissions are listed. */
export const MY_SUBMISSIONS_HREF = "/forms?tab=submissions"

const FORM_CHANGED_MESSAGE =
  "This form was updated while you were filling it in. Check your answers and submit again."
const GENERIC_ERROR_MESSAGE = "Something went wrong. Try again."
const READ_ONLY_MESSAGE = "You can view this form but you can't submit it."
const FOCUSABLE_SELECTOR =
  'input:not([disabled]):not([hidden]), textarea:not([disabled]), button:not([disabled]):not([tabindex="-1"])'

/**
 * The component for every widget the backend can resolve a field to. Keyed
 * by the generated `FormWidget` union, so a widget added on the backend fails
 * typecheck here until it has a component.
 */
const WIDGETS: Record<FormWidget, ComponentType<FormWidgetProps>> = {
  text: TextWidget,
  long_text: TextWidget,
  number: TextWidget,
  toggle: ToggleWidget,
  select: SelectWidget,
  multi_select: TagsWidget,
  cards: CardsWidget,
  tags: TagsWidget,
  date: DateWidget,
  datetime: DateWidget,
  user: UserWidget,
  file: FileWidget,
}

const NOTICE_TONES: Record<
  FormNoticeTone,
  { icon: LucideIcon; className: string; iconClassName: string }
> = {
  info: {
    icon: InfoIcon,
    className: "border-primary/35 bg-primary/5",
    iconClassName: "text-primary",
  },
  success: {
    icon: CircleCheckIcon,
    className: "border-emerald-500/35 bg-emerald-500/5",
    iconClassName: "text-emerald-600 dark:text-emerald-400",
  },
  warning: {
    icon: TriangleAlertIcon,
    className: "border-amber-500/40 bg-amber-500/5",
    iconClassName: "text-amber-600 dark:text-amber-400",
  },
  danger: {
    icon: CircleAlertIcon,
    className: "border-destructive/40 bg-destructive/5",
    iconClassName: "text-destructive",
  },
}

/** A callout with an icon, coloured by tone. Its text is markdown. */
function FormNotice({ text, tone }: Omit<FormNoticeBlock, "type">) {
  const { icon: Icon, className, iconClassName } = NOTICE_TONES[tone]
  return (
    <div
      role="note"
      className={cn(
        "flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm leading-5",
        className
      )}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", iconClassName)} />
      <FormMarkdown className="min-w-0 flex-1">{text}</FormMarkdown>
    </div>
  )
}

/** A block that takes no input: a heading, markdown text, a notice or a divider. */
function FormStaticBlock({ block }: { block: FormBlock }) {
  switch (block.type) {
    case "heading":
      return <h2 className={formStyles.heading}>{block.text}</h2>
    case "text":
      return <FormMarkdown className="leading-5">{block.text}</FormMarkdown>
    case "notice":
      return <FormNotice text={block.text} tone={block.tone} />
    case "divider":
      return <Separator />
    default:
      return null
  }
}

/** Props of {@link FormRenderer}. */
export interface FormRendererProps {
  workspaceId: string
  formId: string
  form: FormPreview
  /**
   * `live` submits through `onSubmit`. `preview` never calls the network on
   * submit: it validates, then shows the confirmation with the reference
   * `PREVIEW`.
   */
  mode: "live" | "preview"
  /** Live mode only. `false` shows the form read-only. Defaults to `true`. */
  canSubmit?: boolean
  /**
   * Sends the visible answers. Reject with the API error as is: the renderer
   * reads `form_validation_error` into per-field errors and shows any other
   * failure above the submit button.
   */
  onSubmit?: (values: FormValues) => Promise<FormSubmissionReceipt>
  /** Uploads one file of a `file` field in live mode. Without it, files stay local. */
  uploadFile?: FormFileUploader
  /** Shown in the footer as "Submitting as ...". */
  submitterEmail?: string
  className?: string
}

/** Starting values for `next` that keep each answer whose widget is unchanged. */
function carryOverValues(
  previous: FormBlock[],
  next: FormBlock[],
  current: FormValues
): FormValues {
  const widgets = new Map(
    previous.filter(isFieldBlock).map((field) => [field.name, field.widget])
  )
  const values = getInitialFormValues(next)
  for (const { name, widget } of next.filter(isFieldBlock)) {
    const kept = widgets.get(name) === widget && current[name] !== undefined
    if (kept) values[name] = current[name]
  }
  return values
}

/** A field's label, help and error around its widget. */
function FormFieldRow({
  error,
  ...props
}: Omit<FormWidgetProps, "invalid"> & { error?: string }) {
  const { id, field } = props
  const Widget = WIDGETS[field.widget]
  // A newer backend can send a widget this build has no component for.
  if (!Widget) return null
  const label = (
    <label id={`${id}-label`} htmlFor={id} className={formStyles.label}>
      {field.label}
      {field.required && (
        <span aria-hidden className={formStyles.required}>
          {" *"}
        </span>
      )}
    </label>
  )
  const help = field.help && (
    <p id={`${id}-help`} className={formStyles.help}>
      {field.help}
    </p>
  )
  const widget = <Widget {...props} invalid={Boolean(error)} />
  return (
    <div id={`${id}-row`} className={formStyles.field}>
      {field.widget === "toggle" ? (
        <div className="flex items-center justify-between gap-3">
          <div>
            {label}
            {help}
          </div>
          {widget}
        </div>
      ) : (
        <>
          {label}
          {widget}
          {help}
        </>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className={formStyles.error}>
          {error}
        </p>
      )}
    </div>
  )
}

/**
 * Render a resolved form: its header, blocks, fields and submit button, and
 * the confirmation that replaces them after a submission.
 *
 * Shared by the requester page (`mode="live"`) and the builder preview
 * (`mode="preview"`). Conditional fields follow the answers as they change;
 * a hidden field is not rendered, not validated and not sent. A change clears
 * the field's error, and a failed submit focuses the first invalid field.
 */
export function FormRenderer({
  workspaceId,
  formId,
  form,
  mode,
  canSubmit = true,
  onSubmit,
  uploadFile,
  submitterEmail,
  className,
}: FormRendererProps) {
  const baseId = useId()
  const { blocks } = form
  const [values, setValues] = useState(() => getInitialFormValues(blocks))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [formErrors, setFormErrors] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<FormSubmissionReceipt | null>(null)

  // The form can change under the requester: a reload after `form_changed`,
  // or every edit in the builder preview. Keep the answers that still apply.
  const [shownBlocks, setShownBlocks] = useState(blocks)
  if (shownBlocks !== blocks) {
    setShownBlocks(blocks)
    setValues(carryOverValues(shownBlocks, blocks, values))
    setErrors({})
  }

  // Controls are disabled while a submit is in flight and a disabled control
  // cannot take focus, so the names wait here until the submit has settled.
  const invalidNames = useRef<string[] | null>(null)
  useEffect(() => {
    if (submitting || !invalidNames.current) return
    focusFirstError(invalidNames.current)
    invalidNames.current = null
  })

  // DOM ids come from the block index: an input name can hold characters,
  // such as spaces, that would split an id inside `aria-describedby`.
  const fieldId = (index: number) => `${baseId}-${index}`

  /** Scroll to the first invalid field and move keyboard focus into it. */
  function focusFirstError(names: string[]) {
    const index = blocks.findIndex(
      (block) => isFieldBlock(block) && names.includes(block.name)
    )
    if (index === -1) return
    const id = fieldId(index)
    document
      .getElementById(`${id}-row`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" })
    // Most widgets put the id on the control. Cards put it on the group, so
    // fall back to the first control inside that can take focus.
    const control = document.getElementById(id)
    const target = control?.matches(FOCUSABLE_SELECTOR)
      ? control
      : control?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR)
    target?.focus({ preventScroll: true })
  }

  function setValue(name: string, value: unknown) {
    setValues((current) => ({ ...current, [name]: value }))
    setErrors(({ [name]: _cleared, ...rest }) => rest)
  }

  function showSubmitError(error: unknown) {
    const detail = getFormErrorDetail(error)
    if (detail?.code === "form_changed") {
      setFormErrors([FORM_CHANGED_MESSAGE])
      return
    }
    if (detail?.code !== "form_validation_error") {
      setFormErrors([detail?.message ?? GENERIC_ERROR_MESSAGE])
      return
    }
    // An error for a field on screen goes on that field, and the last one
    // wins: later errors come from later, more specific checks. The rest go
    // above the submit button.
    const visible = new Set(
      getVisibleFields(blocks, values).map((field) => field.name)
    )
    const fieldErrors: Record<string, string> = {}
    const unplaced: string[] = []
    for (const { field, message } of detail.errors ?? []) {
      if (visible.has(field)) fieldErrors[field] = message
      else unplaced.push(`${field}: ${message}`)
    }
    const names = Object.keys(fieldErrors)
    setErrors(fieldErrors)
    // A rejection that names no field still has to say something.
    setFormErrors(
      unplaced.length > 0 || names.length > 0 ? unplaced : [detail.message]
    )
    invalidNames.current = names
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const invalid = validateFormValues(blocks, values)
    setErrors(invalid)
    if (Object.keys(invalid).length > 0) {
      invalidNames.current = Object.keys(invalid)
      return
    }
    setFormErrors([])
    if (mode === "preview") {
      setReceipt({
        id: "preview",
        reference: "PREVIEW",
        submitted_at: new Date().toISOString(),
        success_message: form.success_message,
      })
      return
    }
    if (!onSubmit || !canSubmit) return
    setSubmitting(true)
    try {
      setReceipt(await onSubmit(toSubmissionValues(blocks, values)))
    } catch (error) {
      showSubmitError(error)
    } finally {
      setSubmitting(false)
    }
  }

  // What a requester sees after submitting: a confirmation, the optional
  // success message and a reference to quote. Never the workflow run.
  if (receipt) {
    const successMessage = receipt.success_message ?? form.success_message
    return (
      <div className={cn(formStyles.card, "items-center gap-4 text-center")}>
        <span className="flex size-12 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
          <CheckIcon className="size-6" />
        </span>
        <div>
          <h1 className={formStyles.title}>Request submitted</h1>
          {successMessage && (
            <p className={formStyles.description}>{successMessage}</p>
          )}
        </div>
        <div className="flex items-center gap-3 self-stretch rounded-md border bg-muted/50 py-2 pl-3 pr-2 text-left">
          <div className="min-w-0 flex-1">
            <div className={formStyles.help}>Reference</div>
            <div className="truncate font-mono text-sm font-medium">
              {receipt.reference}
            </div>
          </div>
          <CopyButton
            value={receipt.reference}
            toastMessage="Copied reference to clipboard"
            tooltipMessage="Copy reference"
            className="size-8"
            iconClassName="size-4"
          />
        </div>
        <div className="flex flex-wrap gap-2 self-stretch [&>*]:flex-1">
          <Button
            type="button"
            variant="outline"
            className={formStyles.button}
            onClick={() => {
              setValues(getInitialFormValues(blocks))
              setErrors({})
              setFormErrors([])
              setReceipt(null)
            }}
          >
            Submit another
          </Button>
          {mode === "live" && (
            <Button asChild variant="outline" className={formStyles.button}>
              <Link href={MY_SUBMISSIONS_HREF}>My submissions</Link>
            </Button>
          )}
        </div>
      </div>
    )
  }

  const readOnly = mode === "live" && !canSubmit
  const disabled = readOnly || submitting
  const visibleNames = new Set(
    getVisibleFields(blocks, values).map((field) => field.name)
  )

  return (
    <form
      noValidate
      onSubmit={handleSubmit}
      className={cn(formStyles.card, className)}
    >
      <div>
        <h1 className={formStyles.title}>{form.title}</h1>
        {form.description && (
          <FormMarkdown className={formStyles.description}>
            {form.description}
          </FormMarkdown>
        )}
      </div>
      {readOnly && <FormNotice tone="warning" text={READ_ONLY_MESSAGE} />}
      {blocks.map((block, index) => {
        if (!isFieldBlock(block)) {
          // Static blocks have no identity of their own and never reorder
          // without the whole list being replaced.
          return <FormStaticBlock key={`block-${index}`} block={block} />
        }
        if (!visibleNames.has(block.name)) return null
        return (
          <FormFieldRow
            key={block.name}
            id={fieldId(index)}
            field={block}
            value={values[block.name]}
            onChange={(value) => setValue(block.name, value)}
            error={errors[block.name]}
            disabled={disabled}
            formId={formId}
            workspaceId={workspaceId}
            uploadFile={mode === "live" ? uploadFile : undefined}
          />
        )
      })}
      {formErrors.length > 0 && (
        <Alert variant="destructive" className="px-3 py-2.5">
          <AlertDescription className="text-sm">
            {formErrors.map((message) => (
              <p key={message}>{message}</p>
            ))}
          </AlertDescription>
        </Alert>
      )}
      <div className={formStyles.footer}>
        <span>
          {mode === "preview" && "Preview. Nothing is submitted."}
          {mode === "live" &&
            submitterEmail &&
            `Submitting as ${submitterEmail}`}
        </span>
        <Button type="submit" disabled={disabled} className={formStyles.button}>
          {submitting ? "Submitting..." : "Submit"}
        </Button>
      </div>
    </form>
  )
}
