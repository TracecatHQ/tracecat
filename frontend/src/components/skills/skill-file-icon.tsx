import { FileCode2, FileText } from "lucide-react"
import { cn } from "@/lib/utils"

type SkillFileIconProps = {
  path: string
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
    return (
      <FileCode2
        className={cn("size-4 shrink-0 text-muted-foreground", className)}
      />
    )
  }

  return (
    <FileText
      className={cn("size-4 shrink-0 text-muted-foreground", className)}
    />
  )
}
