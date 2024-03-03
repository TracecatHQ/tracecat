import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Textarea } from "@/components/ui/textarea"
import { Separator } from "@/components/ui/separator"
import { Input } from "@/components/ui/input"
 
// Define formSchema for type safety
const workflowFormSchema = z.object({
    name: z.string(),
  description: z.string()
})
 
export function WorkflowForm() {
  const form = useForm<z.infer<typeof workflowFormSchema>>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
        name: "",
    },
  })
 
  function onSubmit(values: z.infer<typeof workflowFormSchema>) {
    console.log(values)
  }

  return (
    <div>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4 p-4">
          <FormField
            control={form.control}
            name="name"
            render={({ field }: { field: any }) => (
              <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                      <Input className="text-xs" placeholder="Add workflow name..." {...field} />
                  </FormControl>
                  <FormMessage />
              </FormItem>
          )}
          />
          <FormField
            control={form.control}
            name="description"
            render={({ field }: { field: any }) => (
              <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                      <Textarea className="text-xs" placeholder="Describe your workflow..." {...field} />
                  </FormControl>
                  <FormMessage />
              </FormItem>
          )}
          />
          <Button className="text-xs" variant="outline" type="submit">Update</Button>
        </form>
      </Form>
      <Separator />
      <Form {...form}>
          <Button className="text-xs" variant="outline" type="submit">Activate</Button>
      </Form>
    </div>
  )
}
