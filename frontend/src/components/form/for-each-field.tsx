import { Plus, Trash2 } from "lucide-react"
import { useFormContext } from "react-hook-form"
import { ExpressionInput } from "@/components/editor/expression-input"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"

export function ForEachField() {
  const { control } = useFormContext()
  return (
    <FormField
      name="for_each"
      control={control}
      render={({ field }) => {
        let value: string[] | null
        if (Array.isArray(field.value)) {
          value = field.value
        } else if (typeof field.value === "string") {
          value = [field.value]
        } else {
          value = null
        }
        return (
          <FormItem>
            <FormMessage className="whitespace-pre-line" />
            <FormControl>
              <div className="space-y-2">
                {value
                  ? value.map((expr, index) => (
                      <div key={index} className="flex gap-2">
                        <ExpressionInput
                          value={expr}
                          onChange={(newValue) => {
                            const newExpressions = [...value]
                            newExpressions[index] = newValue
                            field.onChange(newExpressions)
                          }}
                          placeholder="Type @foreach to begin a for loop expression..."
                          className="flex-1"
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          type="button"
                          onClick={() => {
                            const newExpressions = value.filter(
                              (_: string, i: number) => i !== index
                            )
                            field.onChange(newExpressions)
                          }}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </div>
                    ))
                  : null}
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 w-full"
                  type="button"
                  onClick={() => {
                    const newExpressions = [...(value || []), ""]
                    field.onChange(newExpressions)
                  }}
                >
                  <Plus className="mr-2 size-4" />
                  Add Expression
                </Button>
              </div>
            </FormControl>
          </FormItem>
        )
      }}
    />
  )
}
