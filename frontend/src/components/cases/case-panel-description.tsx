import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2Icon, PencilIcon, SaveIcon } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseRead, CaseUpdate } from "@/client"
import { CaseDescriptionEditor } from "@/components/cases/case-description-editor"
import { Button } from "@/components/tiptap-ui-primitive/button"
import { Form, FormControl, FormField, FormItem } from "@/components/ui/form"

const descriptionFormSchema = z.object({
  description: z.string().optional(),
})
type DescriptionFormSchema = z.infer<typeof descriptionFormSchema>

interface CasePanelDescriptionProps {
  caseData: CaseRead
  updateCase: (caseData: CaseUpdate) => Promise<void>
}

export function CasePanelDescription({
  caseData,
  updateCase,
}: CasePanelDescriptionProps) {
  const [isMacPlatform, setIsMacPlatform] = useState(false)

  const form = useForm<DescriptionFormSchema>({
    resolver: zodResolver(descriptionFormSchema),
    defaultValues: {
      description: caseData?.description || "",
    },
  })

  // Reset form when caseData changes to avoid false dirty states
  useEffect(() => {
    form.reset({
      description: caseData?.description || "",
    })
  }, [caseData, form])

  const handleSave = useCallback(
    async (values: DescriptionFormSchema) => {
      if (values.description === caseData?.description) {
        return // No changes to save
      }

      try {
        await updateCase({ description: values.description })
        form.reset({ description: values.description })
      } catch (error) {
        console.error("Failed to save description", error)
      }
    },
    [updateCase, caseData, form]
  )

  // Save on blur, mirroring other editor forms
  const handleBlur = useCallback(() => {
    if (form.formState.isDirty) {
      void form.handleSubmit(handleSave)()
    }
  }, [form, handleSave])

  useEffect(() => {
    if (typeof navigator === "undefined") {
      return
    }

    setIsMacPlatform(/Mac|iPod|iPhone|iPad/.test(navigator.userAgent))
  }, [])

  // Setup keyboard shortcut for saving with platform-specific modifier
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const shouldHandle =
        (isMacPlatform && e.metaKey) || (!isMacPlatform && e.ctrlKey)

      if (!shouldHandle) {
        return
      }

      if (e.key.toLowerCase() !== "s") {
        return
      }

      e.preventDefault()
      void form.handleSubmit(handleSave)()
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [form, handleSave, isMacPlatform])

  const { isDirty, isSubmitting } = form.formState

  const toolbarStatus = useMemo(() => {
    if (isSubmitting) {
      return (
        <Button
          type="button"
          data-style="ghost"
          aria-label="Saving description"
          tooltip="Saving..."
          disabled
          data-disabled
        >
          <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
        </Button>
      )
    }

    if (isDirty) {
      const shortcut = isMacPlatform ? "⌘+S" : "Ctrl+S"
      return (
        <Button
          type="button"
          data-style="ghost"
          aria-label="Save case description"
          tooltip={
            <span className="flex items-center gap-2">
              <span>Save changes</span>
              <span className="font-mono text-xs text-muted-foreground/80">
                {shortcut}
              </span>
            </span>
          }
          onClick={() => void form.handleSubmit(handleSave)()}
        >
          <SaveIcon className="size-4 text-muted-foreground" />
        </Button>
      )
    }

    return (
      <Button
        type="button"
        data-style="ghost"
        aria-label="Description saved"
        tooltip="Description up to date"
        disabled
        data-disabled
      >
        <PencilIcon className="size-4 text-muted-foreground" />
      </Button>
    )
  }, [form, handleSave, isMacPlatform, isDirty, isSubmitting])

  return (
    <div className="relative">
      <Form {...form}>
        <form className="space-y-2" onSubmit={form.handleSubmit(handleSave)}>
          <FormField
            control={form.control}
            name="description"
            render={({ field }) => (
              <FormItem className="relative">
                <FormControl>
                  <CaseDescriptionEditor
                    className="min-h-[250px]"
                    initialContent={caseData.description}
                    onChange={(content) => {
                      field.onChange(content)
                    }}
                    onBlur={handleBlur}
                    toolbarStatus={toolbarStatus}
                  />
                </FormControl>
              </FormItem>
            )}
          />
        </form>
      </Form>
    </div>
  )
}
