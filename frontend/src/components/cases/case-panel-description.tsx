import React, { useCallback, useEffect, useState } from "react"
import { CaseRead, CaseUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  CircleCheckIcon,
  Loader2Icon,
  SaveIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import * as z from "zod"

import { cn } from "@/lib/utils"
import { Form, FormControl, FormField, FormItem } from "@/components/ui/form"
import { CaseDescriptionEditor } from "@/components/cases/case-description-editor"

const descriptionFormSchema = z.object({
  description: z.string().optional(),
})
type DescriptionFormSchema = z.infer<typeof descriptionFormSchema>

enum SaveState {
  IDLE = "idle",
  UNSAVED = "unsaved",
  SAVING = "saving",
  SAVED = "saved",
  ERROR = "error",
}

interface CasePanelDescriptionProps {
  caseData: CaseRead
  updateCase: (caseData: CaseUpdate) => Promise<void>
}

export function CasePanelDescription({
  caseData,
  updateCase,
}: CasePanelDescriptionProps) {
  const [saveState, setSaveState] = useState<SaveState>(SaveState.IDLE)

  const form = useForm<DescriptionFormSchema>({
    resolver: zodResolver(descriptionFormSchema),
    defaultValues: {
      description: caseData?.description || "",
    },
    values: {
      description: caseData?.description || "",
    },
  })

  // Update save state when form state changes
  useEffect(() => {
    if (form.formState.isDirty) {
      setSaveState(SaveState.UNSAVED)
    }
  }, [form.formState.isDirty])

  const handleSave = useCallback(
    async (values: DescriptionFormSchema) => {
      if (values.description === caseData?.description) {
        return // No changes to save
      }

      setSaveState(SaveState.SAVING)
      try {
        await updateCase({ description: values.description })
        setSaveState(SaveState.SAVED)
        form.reset({ description: values.description })
        // Reset to IDLE after 2 seconds
        setTimeout(() => setSaveState(SaveState.IDLE), 2000)
      } catch (error) {
        console.error("Failed to save description", error)
        setSaveState(SaveState.ERROR)
      } finally {
        setSaveState(SaveState.IDLE)
      }
    },
    [updateCase, caseData, form]
  )

  // Handle content change from editor
  const handleContentChange = useCallback(
    (markdown: string) => {
      form.setValue("description", markdown, { shouldDirty: true })
    },
    [form]
  )

  // Save on blur
  const handleBlur = () => {
    if (form.formState.isDirty) {
      form.handleSubmit(handleSave)()
    }
  }

  // Setup keyboard shortcut for saving (Cmd+Enter or Ctrl+Enter)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault()
        form.handleSubmit(handleSave)()
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [form, handleSave])

  return (
    <div className="relative">
      <Form {...form}>
        <form className="space-y-2" onSubmit={form.handleSubmit(handleSave)}>
          <FormField
            control={form.control}
            name="description"
            render={() => (
              <FormItem className="relative">
                <FormControl>
                  <CaseDescriptionEditor
                    className="min-h-[250px]"
                    initialContent={caseData.description}
                    onChange={handleContentChange}
                    onBlur={handleBlur}
                  />
                </FormControl>
                <div
                  className={cn(
                    "absolute bottom-4 right-4 z-10 flex items-center justify-end space-x-2",
                    "transition-all duration-300 ease-in-out",
                    saveState === SaveState.IDLE && "opacity-0"
                  )}
                >
                  {saveState === SaveState.UNSAVED && (
                    <>
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        width="24"
                        height="24"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        className="size-3 text-muted-foreground"
                      >
                        <path d="M13 13H8a1 1 0 0 0-1 1v7" />
                        <path d="M14 8h1" />
                        <path d="M17 21v-4" />
                        <path d="m2 2 20 20" />
                        <path d="M20.41 20.41A2 2 0 0 1 19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 .59-1.41" />
                        <path d="M29.5 11.5s5 5 4 5" />
                        <path d="M9 3h6.2a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V15" />
                      </svg>
                      <span className="text-xs text-muted-foreground">
                        Unsaved
                      </span>
                      <span className="my-px ml-auto flex items-center space-x-2">
                        <div className="mx-1 my-0 flex items-center space-x-1 rounded-sm border border-muted-foreground/20 bg-muted-foreground/10 px-px py-0 font-mono text-xs text-muted-foreground/80">
                          <SaveIcon className="size-3 text-muted-foreground/70" />
                          <p>
                            {typeof navigator.userAgent !== "undefined"
                              ? /Mac|iPod|iPhone|iPad/.test(navigator.userAgent)
                                ? "⌘+Enter"
                                : "Ctrl+Enter"
                              : "Ctrl+Enter"}
                          </p>
                        </div>
                      </span>
                    </>
                  )}
                  {saveState === SaveState.SAVING && (
                    <>
                      <Loader2Icon className="size-3 animate-spin text-muted-foreground" />
                      <span className="text-xs text-muted-foreground">
                        Saving
                      </span>
                    </>
                  )}
                  {saveState === SaveState.SAVED && (
                    <>
                      <CircleCheckIcon className="size-4 fill-emerald-500 stroke-white" />
                      <span className="text-xs text-emerald-600">Saved</span>
                    </>
                  )}
                  {saveState === SaveState.ERROR && (
                    <>
                      <AlertTriangleIcon className="size-4 fill-rose-500 stroke-white" />
                      <span className="text-xs text-rose-500">Error</span>
                    </>
                  )}
                </div>
              </FormItem>
            )}
          />
        </form>
      </Form>
    </div>
  )
}
