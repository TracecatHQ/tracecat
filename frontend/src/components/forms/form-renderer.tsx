"use client"

import Link from "next/link"
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import {
  Controller,
  type FieldErrors,
  type Resolver,
  useForm,
} from "react-hook-form"
import type {
  FormFieldBlock,
  FormResolved,
  FormSubmissionReceipt,
} from "@/client"
import {
  FormConfirmation,
  FormHeader,
  FormNotice,
  FormStaticBlock,
} from "@/components/forms/form-blocks"
import { formStyles } from "@/components/forms/form-styles"
import { FORM_WIDGETS } from "@/components/forms/widgets/registry"
import type {
  FormFileUploader,
  FormWidgetProps,
  FormWidgetRuntime,
} from "@/components/forms/widgets/types"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { getFormErrorDetail, mapFormFieldErrors } from "@/lib/forms/errors"
import {
  fromFieldKey,
  fromFieldKeyValues,
  toFieldKey,
  toFieldKeyValues,
} from "@/lib/forms/field-keys"
import { getInitialFormValues, validateFormValues } from "@/lib/forms/validate"
import {
  type FormBlock,
  type FormValues,
  getVisibleFields,
  isFieldBlock,
  toSubmissionValues,
} from "@/lib/forms/visibility"
import { cn } from "@/lib/utils"

/** Where a requester's own submissions are listed. */
export const MY_SUBMISSIONS_HREF = "/forms?tab=submissions"

const FORM_CHANGED_MESSAGE =
  "This form was updated while you were filling it in. Check your answers and submit again."
const GENERIC_ERROR_MESSAGE = "Something went wrong. Try again."
const READ_ONLY_MESSAGE = "You can view this form but you can't submit it."
const FOCUSABLE_SELECTOR =
  'input:not([disabled]):not([hidden]), textarea:not([disabled]), button:not([disabled]):not([tabindex="-1"])'

/** Props of {@link FormRenderer}. */
export interface FormRendererProps {
  formId: string
  form: FormResolved
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
  /** Uploads one file of a `file` field. Without it, files stay local. */
  uploadFile?: FormFileUploader
  /** Shown in the footer as "Submitting as ...". */
  submitterEmail?: string
  className?: string
}

function carryOverValues(
  previous: FormBlock[],
  next: FormBlock[],
  current: FormValues
): FormValues {
  const previousWidgets = new Map(
    previous.filter(isFieldBlock).map((field) => [field.name, field.widget])
  )
  const values = getInitialFormValues(next)
  for (const field of next.filter(isFieldBlock)) {
    if (
      previousWidgets.get(field.name) === field.widget &&
      current[field.name] !== undefined
    ) {
      values[field.name] = current[field.name]
    }
  }
  return values
}

function FormFieldRow({
  id,
  field,
  error,
  widgetProps,
}: {
  id: string
  field: FormFieldBlock
  error?: string
  widgetProps: Omit<FormWidgetProps, "id" | "field" | "invalid">
}) {
  const Widget = FORM_WIDGETS[field.widget]
  // A newer backend can send a widget this build has no component for.
  if (!Widget) {
    return null
  }
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
  const message = error && (
    <p id={`${id}-error`} role="alert" className={formStyles.error}>
      {error}
    </p>
  )
  const widget = (
    <Widget id={id} field={field} invalid={Boolean(error)} {...widgetProps} />
  )

  if (field.widget === "toggle") {
    return (
      <div id={`${id}-row`} className={formStyles.field}>
        <div className="flex items-center justify-between gap-3">
          <div>
            {label}
            {help}
          </div>
          {widget}
        </div>
        {message}
      </div>
    )
  }
  return (
    <div id={`${id}-row`} className={formStyles.field}>
      {label}
      {widget}
      {help}
      {message}
    </div>
  )
}

/**
 * Render a resolved form: its header, blocks, fields and submit button, and
 * the confirmation that replaces them after a submission.
 *
 * Shared by the requester page (`mode="live"`) and the builder preview
 * (`mode="preview"`). Conditional fields follow the answers as they change;
 * a hidden field is not rendered, not validated and not sent.
 */
export function FormRenderer({
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
  const blocks = form.blocks
  const [receipt, setReceipt] = useState<FormSubmissionReceipt | null>(null)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // Form state is keyed by `toFieldKey(name)`, never by the input name: a
  // name such as `user.email` would be read as a path by react-hook-form.
  const resolver = useCallback<Resolver<FormValues>>(
    async (values) => {
      const messages = Object.entries(
        validateFormValues(blocks, fromFieldKeyValues(values))
      )
      if (messages.length === 0) {
        return { values, errors: {} }
      }
      return {
        values: {},
        errors: Object.fromEntries(
          messages.map(([name, message]) => [
            toFieldKey(name),
            { type: "validate", message },
          ])
        ) as FieldErrors<FormValues>,
      }
    },
    [blocks]
  )
  const {
    control,
    handleSubmit,
    watch,
    reset,
    getValues,
    setError,
    formState,
  } = useForm<FormValues>({
    defaultValues: toFieldKeyValues(getInitialFormValues(blocks)),
    resolver,
  })

  // The form can change under the requester: a reload after `form_changed`,
  // or every edit in the builder preview. Keep the answers that still apply.
  const previousBlocks = useRef(blocks)
  useEffect(() => {
    if (previousBlocks.current === blocks) {
      return
    }
    reset(
      toFieldKeyValues(
        carryOverValues(
          previousBlocks.current,
          blocks,
          fromFieldKeyValues(getValues())
        )
      )
    )
    previousBlocks.current = blocks
  }, [blocks, reset, getValues])

  const values = fromFieldKeyValues(watch())
  const visibleNames = new Set(
    getVisibleFields(blocks, values).map((field) => field.name)
  )
  const runtime = useMemo<FormWidgetRuntime>(
    () => ({ formId, workspaceId: form.workspace_id, mode, uploadFile }),
    [formId, form.workspace_id, mode, uploadFile]
  )

  // An input name can hold spaces, which would split an id inside
  // `aria-describedby`. The field key has letters, digits and `_` only.
  const fieldId = (name: string) => `${baseId}-${toFieldKey(name)}`

  // Controls are disabled while a submit is in flight and a disabled control
  // cannot take focus, so the names wait here until the submit has settled.
  const invalidNames = useRef<string[] | null>(null)
  const isSubmitting = formState.isSubmitting
  useEffect(() => {
    if (isSubmitting || !invalidNames.current) {
      return
    }
    focusFirstError(invalidNames.current)
    invalidNames.current = null
  }, [isSubmitting])

  /** Scroll to the first invalid field and move keyboard focus into it. */
  function focusFirstError(names: string[]) {
    const invalid = new Set(names)
    const first = blocks
      .filter(isFieldBlock)
      .find((field) => invalid.has(field.name))
    if (!first) {
      return
    }
    const id = fieldId(first.name)
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

  async function submit(state: FormValues) {
    const submitted = fromFieldKeyValues(state)
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
    if (!onSubmit || !canSubmit) {
      return
    }
    try {
      setReceipt(await onSubmit(toSubmissionValues(blocks, submitted)))
    } catch (error) {
      const detail = getFormErrorDetail(error)
      if (detail?.code === "form_validation_error") {
        const { fieldErrors, formErrors: unplaced } = mapFormFieldErrors(
          detail.errors,
          getVisibleFields(blocks, submitted).map((field) => field.name)
        )
        const names = Object.keys(fieldErrors)
        for (const name of names) {
          setError(toFieldKey(name), {
            type: "server",
            message: fieldErrors[name],
          })
        }
        // A rejection that names no field still has to say something.
        setFormErrors(
          unplaced.length > 0 || names.length > 0 ? unplaced : [detail.message]
        )
        invalidNames.current = names
      } else if (detail?.code === "form_changed") {
        setFormErrors([FORM_CHANGED_MESSAGE])
      } else {
        setFormErrors([detail?.message ?? GENERIC_ERROR_MESSAGE])
      }
    }
  }

  function submitAnother() {
    reset(toFieldKeyValues(getInitialFormValues(blocks)))
    setFormErrors([])
    setReceipt(null)
  }

  if (receipt) {
    return (
      <FormConfirmation
        receipt={receipt}
        successMessage={receipt.success_message ?? form.success_message}
        actions={
          <>
            <Button
              type="button"
              variant="outline"
              className={formStyles.button}
              onClick={submitAnother}
            >
              Submit another
            </Button>
            {mode === "live" && (
              <Button asChild variant="outline" className={formStyles.button}>
                <Link href={MY_SUBMISSIONS_HREF}>My submissions</Link>
              </Button>
            )}
          </>
        }
      />
    )
  }

  const readOnly = mode === "live" && !canSubmit
  const disabled = readOnly || formState.isSubmitting

  return (
    <form
      noValidate
      onSubmit={handleSubmit(submit, (errors) => {
        invalidNames.current = Object.keys(errors).map(fromFieldKey)
      })}
      className={cn(formStyles.card, className)}
    >
      <FormHeader title={form.title} description={form.description} />
      {readOnly && <FormNotice tone="warning" text={READ_ONLY_MESSAGE} />}
      {blocks.map((block, index) => {
        if (!isFieldBlock(block)) {
          // Static blocks have no identity of their own and never reorder
          // without the whole list being replaced.
          return <FormStaticBlock key={`block-${index}`} block={block} />
        }
        if (!visibleNames.has(block.name)) {
          return null
        }
        return (
          <Controller
            key={block.name}
            control={control}
            name={toFieldKey(block.name)}
            render={({ field, fieldState }) => (
              <FormFieldRow
                id={fieldId(block.name)}
                field={block}
                error={fieldState.error?.message}
                widgetProps={{
                  value: field.value,
                  onChange: field.onChange,
                  onBlur: field.onBlur,
                  disabled,
                  runtime,
                }}
              />
            )}
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
          {formState.isSubmitting ? "Submitting..." : "Submit"}
        </Button>
      </div>
    </form>
  )
}
