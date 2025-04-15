import { useEffect } from "react"

export function useDeleteKey({
  onDelete,
  predicate,
}: {
  onDelete: () => void
  predicate?: () => boolean
}) {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      let shouldDelete = predicate?.() ?? true
      if ((e.key === "Delete" || e.key === "Backspace") && shouldDelete) {
        onDelete()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [onDelete])
}
