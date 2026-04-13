import { FileText } from "lucide-react"
import { isMarkdownPath } from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

type SkillFileIconProps = {
  path: string
  className?: string
}

type FileIconSvgProps = {
  className?: string
}

/**
 * Render a compact file-type marker for skill files.
 *
 * @param props File path and optional class name.
 * @returns A small file icon or extension badge.
 *
 * @example
 * <SkillFileIcon path="scripts/helper.py" />
 */
export function SkillFileIcon({ path, className }: SkillFileIconProps) {
  if (path.endsWith(".py")) {
    return <PythonFileIcon className={className} />
  }

  if (isMarkdownPath(path)) {
    return (
      <span
        className={cn(
          "inline-flex size-4 shrink-0 items-center justify-center rounded-[4px] border text-[8px] font-semibold uppercase leading-none",
          "border-border bg-muted text-foreground",
          className
        )}
        aria-hidden="true"
      >
        md
      </span>
    )
  }

  return (
    <FileText
      className={cn("size-4 shrink-0 text-muted-foreground", className)}
    />
  )
}

function PythonFileIcon({ className }: FileIconSvgProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className={cn("size-4 shrink-0", className)}
    >
      <path
        d="M4.5 1.5h4.6l2.9 2.9v8.1a2 2 0 0 1-2 2h-5.5a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z"
        className="fill-background stroke-border"
        strokeWidth="1"
      />
      <path d="M9.1 1.5v2.2c0 .39.31.7.7.7H12" className="stroke-border" />
      <path
        d="M6 5.1h1.9c.7 0 1.2.56 1.2 1.25v1.05c0 .69-.5 1.25-1.2 1.25H6.8a.8.8 0 0 0-.8.8v.45c0 .69.5 1.25 1.2 1.25h1.8"
        fill="#3776AB"
      />
      <circle cx="7.3" cy="6.25" r=".45" fill="white" />
      <path
        d="M10 10.9H8.1c-.7 0-1.2-.56-1.2-1.25V8.6c0-.69.5-1.25 1.2-1.25h1.1a.8.8 0 0 0 .8-.8V6.1c0-.69-.5-1.25-1.2-1.25H7"
        fill="#FFD43B"
      />
      <circle cx="8.7" cy="9.75" r=".45" fill="#5C4B00" />
    </svg>
  )
}
