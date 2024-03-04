import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"

import { CircleIcon, Save } from "lucide-react"

// Define formSchema for type safety
const actionFormSchema = z.object({
  name: z.string(),
  description: z.string()
})

export function ActionForm() {
  const form = useForm<z.infer<typeof actionFormSchema>>({
    resolver: zodResolver(actionFormSchema),
    defaultValues: {
      name: "",
      description: "",
    },
  })

  function onSubmit(values: z.infer<typeof actionFormSchema>) {
    console.log(values)
  }

  return (
    <ScrollArea>
      <div className="space-y-4 p-4">
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Action Status</h4>
          <div className="flex justify-between">
            <Badge variant="outline" className="bg-green-100 py-1 px-4">
              <CircleIcon className="mr-1 h-3 w-3 fill-green-600 text-green-600" />
              <span className="text-green-600">Online</span>
            </Badge>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button size="icon">
                  <Save className="h-4 w-4" />
                  <span className="sr-only">Save</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Save</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <Separator />
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <div className="space-y-4 mb-4">
              <FormField
                control={form.control}
                name="name"
                render={({ field }: { field: any }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input className="text-xs" placeholder="Add action name..." {...field} />
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
                      <Textarea className="text-xs" placeholder="Describe your action..." {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Separator />
              <div className="space-y-2">
                <h4 className="text-m font-medium">Action Inputs</h4>
                <p className="text-xs text-muted-foreground">
                  Define the inputs for this action.
                </p>
              </div>
            </div>
          </form>
        </Form>
      </div>
    </ScrollArea>
  )
}
