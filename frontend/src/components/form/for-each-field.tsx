import { Plus, Trash2 } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { ExpressionInput } from "@/components/editor/expression-input"

export function ForEachField() {
  const { control } = useFormContext()
  return (
    <FormField
      name="for_each"
      control={control}
      render={({ field }) => (
        <FormItem>
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <div className="space-y-2">
              {Array.isArray(field.value)
                ? field.value.map((expr, index) => (
                    <div key={index} className="flex gap-2">
                      <ExpressionInput
                        value={expr}
                        onChange={(newValue) => {
                          const newExpressions = [...field.value]
                          newExpressions[index] = newValue
                          field.onChange(newExpressions)
                        }}
                        placeholder="Type @foreach to begin a for loop expression..."
                        className="flex-1"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          const newExpressions = field.value.filter(
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
                onClick={() => {
                  const newExpressions = Array.isArray(field.value)
                    ? [...field.value, ""]
                    : [""]
                  field.onChange(newExpressions)
                }}
              >
                <Plus className="mr-2 size-4" />
                Add Expression
              </Button>
            </div>
          </FormControl>
        </FormItem>
      )}
    />
  )
}
