"use client"

import {
  CheckIcon,
  CircleAlertIcon,
  CircleCheckIcon,
  InfoIcon,
  type LucideIcon,
  TriangleAlertIcon,
} from "lucide-react"
import type { ReactNode } from "react"
import type {
  FormNoticeBlock,
  FormNoticeTone,
  FormSubmissionReceipt,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { FormMarkdown } from "@/components/forms/form-markdown"
import { formStyles } from "@/components/forms/form-styles"
import { Separator } from "@/components/ui/separator"
import type { FormBlock } from "@/lib/forms/visibility"
import { cn } from "@/lib/utils"

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
export function FormNotice({ text, tone }: Omit<FormNoticeBlock, "type">) {
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

/**
 * Render a block that takes no input: a heading, markdown text, a notice or
 * a divider. Field blocks are rendered by the form renderer and return null
 * here.
 */
export function FormStaticBlock({ block }: { block: FormBlock }) {
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

/** Title and markdown description at the top of a form. */
export function FormHeader({
  title,
  description,
}: {
  title: string
  description?: string | null
}) {
  return (
    <div>
      <h1 className={formStyles.title}>{title}</h1>
      {description && (
        <FormMarkdown className={formStyles.description}>
          {description}
        </FormMarkdown>
      )}
    </div>
  )
}

/**
 * What a requester sees after submitting: a confirmation, the optional
 * success message and a reference to quote. Never shows the workflow run.
 */
export function FormConfirmation({
  receipt,
  successMessage,
  actions,
}: {
  receipt: FormSubmissionReceipt
  successMessage?: string | null
  /** Buttons shown under the reference. */
  actions?: ReactNode
}) {
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
      {actions && (
        <div className="flex flex-wrap gap-2 self-stretch [&>*]:flex-1">
          {actions}
        </div>
      )}
    </div>
  )
}
