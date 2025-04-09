import { CaseCustomFieldRead, CaseUpdate } from "@/client"
import { useCasePanelContext } from "@/providers/case-panel"
import { useWorkspace } from "@/providers/workspace"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"

import { useUpdateCase } from "@/lib/hooks"
import { Checkbox } from "@/components/ui/checkbox"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"

const customFieldFormSchema = z.object({
  id: z.string(),
  value: z.unknown(),
})

type CustomFieldFormSchema = z.infer<typeof customFieldFormSchema>

export function CustomField({
  customField,
}: {
  customField: CaseCustomFieldRead
}) {
  const { workspaceId } = useWorkspace()
  const { panelCase } = useCasePanelContext()
  const { updateCase } = useUpdateCase({
    caseId: panelCase?.id ?? "",
    workspaceId: workspaceId,
  })
  const form = useForm<CustomFieldFormSchema>({
    defaultValues: {
      id: customField.id,
      value: customField.value,
    },
  })
  const onSubmit = async (data: CustomFieldFormSchema) => {
    if (!panelCase) {
      return
    }
    const caseUpdate: Partial<CaseUpdate> = {
      fields: {
        [customField.id]: data.value,
      },
    }
    console.log("caseUpdate", caseUpdate)
    try {
      await updateCase(caseUpdate)
    } catch (error) {
      console.error(error)
    }
  }
  const onBlur = (id: string, value: unknown) => {
    console.log("onblur", { id, value })
    form.setValue("value", value)
    form.handleSubmit(onSubmit)()
  }
  return (
    <FormProvider {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <CustomFieldInner customField={customField} onBlur={onBlur} />
      </form>
    </FormProvider>
  )
}
interface CustomFieldProps {
  customField: CaseCustomFieldRead
  onBlur?: (id: string, value: unknown) => void
}

/**
 * We wnat to dispatch a form field
 * @param param0
 * @returns
 */
export function CustomFieldInner({ customField, onBlur }: CustomFieldProps) {
  const form = useFormContext<CustomFieldFormSchema>()
  switch (customField.type) {
    case "TEXT":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  {...field}
                  placeholder="Empty"
                  value={String(field.value || "")}
                  variant="flat"
                  onBlur={() => onBlur && onBlur(customField.id, field.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    case "DECIMAL":
    case "INTEGER":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="number"
                  {...field}
                  value={Number(field.value || 0)}
                  onChange={(e) => field.onChange(Number(e.target.value))}
                  variant="flat"
                  onBlur={() =>
                    onBlur && onBlur(customField.id, Number(field.value))
                  }
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    case "BOOLEAN":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Checkbox
                  checked={Boolean(field.value)}
                  onCheckedChange={(checked) => {
                    field.onChange(checked)
                    onBlur && onBlur(customField.id, checked)
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
    case "JSONB":
      return (
        <FormField
          control={form.control}
          name="value"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  type="text"
                  {...field}
                  value={String(field.value || "")}
                  variant="flat"
                  onBlur={() => onBlur && onBlur(customField.id, field.value)}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      )
  }
}
