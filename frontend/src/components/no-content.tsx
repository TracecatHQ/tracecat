import { cn } from "@/lib/utils"

export default function NoContent({
  message,
  className,
}: {
  message: string
  className?: string
}) {
  return (
    <span
      className={cn(
        "flex size-full items-center justify-center text-center text-xs text-muted-foreground",
        className
      )}
    >
      {message}
    </span>
  )
}
