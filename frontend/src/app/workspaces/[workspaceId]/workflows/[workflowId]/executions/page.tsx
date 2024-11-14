"use client"

export default function WorkflowExecutionsPage() {
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">
        Select a workflow run
      </h1>
      <span className="text-center text-sm text-muted-foreground">
        Click on a workflow run in the sidebar to view events.
      </span>
    </main>
  )
}
