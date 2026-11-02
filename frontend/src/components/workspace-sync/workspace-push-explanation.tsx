/** Explain workspace Push's existing draft export policy without blocking it. */
export function WorkspacePushExplanation() {
  return (
    <p className="text-xs text-muted-foreground">
      Workspace Push can export new workflows from their current edits without
      publishing them. To use Push from an individual new workflow, publish it
      first.
    </p>
  )
}
