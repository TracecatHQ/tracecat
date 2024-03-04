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
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { CircleIcon, Save } from "lucide-react"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"

// Define formSchema for type safety
const workflowFormSchema = z.object({
  name: z.string(),
  description: z.string()
})

export function WorkflowForm() {

  const { selectedWorkflowMetadata, setSelectedWorkflowMetadata } = useSelectedWorkflowMetadata()
  const status = selectedWorkflowMetadata.status || "offline"
  const statusCapitalized = status[0].toUpperCase() + status.slice(1);

  const form = useForm<z.infer<typeof workflowFormSchema>>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      name: selectedWorkflowMetadata.title || "",
      description: selectedWorkflowMetadata.description || "",
    },
  })

  function onSubmit(values: z.infer<typeof workflowFormSchema>) {
    console.log(values)
  }

  return (
    <Form {...form}>
      <div className="space-y-4 p-4">
        <div className="space-y-3">
          <h4 className="text-sm font-medium">Workflow Status</h4>
          <div className="flex justify-between">
            <Badge variant="outline" className={`py-1 px-4 ${status === 'online' ? 'bg-green-100' : 'bg-gray-100'}`}>
              <CircleIcon className={`mr-1 h-3 w-3 ${status === 'online' ? 'fill-green-600 text-green-600' : 'fill-gray-400 text-gray-400'}`} />
              <span className={`${status === 'online' ? 'text-green-600' : 'text-gray-600'}`}>{statusCapitalized}</span>
            </Badge>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button size="icon">
                  <Save className="h-4 w-4" />
                  <span className="sr-only">Save</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Archive</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <Separator />
        <div className="space-y-4">
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
          </form>
        </div>
      </div>
    </Form>
  )
}
