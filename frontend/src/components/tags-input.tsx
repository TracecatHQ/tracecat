import { useState } from "react"
import { TagInput as EmblorTagInput } from "emblor"
import type { TagInputProps } from "emblor"

// Define the props we want to expose to consumers
type CustomTagInputProps = Omit<
  TagInputProps,
  "activeTagIndex" | "setActiveTagIndex" | "styleClasses"
>

/**
 * Wrapper component for Emblor's TagInput that internally manages activeTagIndex state
 * and applies consistent styling across the application
 */
export function CustomTagInput(props: CustomTagInputProps) {
  const [activeTagIndex, setActiveTagIndex] = useState<number | null>(null)

  return (
    <EmblorTagInput
      {...props}
      activeTagIndex={activeTagIndex}
      setActiveTagIndex={setActiveTagIndex}
      styleClasses={{
        input: "shadow-none text-xs",
        inlineTagsContainer: "shadow-sm h-9 items-center border text-xs",
        tag: {
          body: "h-5 text-xs",
        },
      }}
    />
  )
}
