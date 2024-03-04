import axios from "axios";
import { useMutation } from "@tanstack/react-query";

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
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { CircleIcon, Save } from "lucide-react"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"

// Define formSchema for type safety
const workflowFormSchema = z.object({
  title: z.string(),
  description: z.string()
})

interface WorkflowFormProps {
  workflowId: string;
  workflowTitle: string;
  workflowDescription: string;
  workflowStatus: string;
}


export function WorkflowForm({ workflowId, workflowTitle, workflowDescription, workflowStatus }: WorkflowFormProps): React.JSX.Element {

  const statusCapitalized = workflowStatus[0].toUpperCase() + workflowStatus.slice(1);

  const form = useForm<z.infer<typeof workflowFormSchema>>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflowTitle || "",
      description: workflowDescription || "",
    },
  })

  // Submit form and update Workflow
  async function updateWorkflow(workflowId: string, values: z.infer<typeof workflowFormSchema>) {
    const response = await axios.post(`http://localhost:8000/workflows/${workflowId}`, values);
    return response.data; // Adjust based on what your API returns
  }

  function useUpdateWorkflow(workflowId: string) {
    const mutation = useMutation({
      mutationFn: (values: z.infer<typeof workflowFormSchema>) => updateWorkflow(workflowId, values),
      // Configure your mutation behavior here
      onSuccess: (data, variables, context) => {
        console.log("Workflow update successful", data);
      },
      onError: (error, variables, context) => {
        console.error("Failed to update workflow:", error);
      },
    });

    return mutation;
  }

  // TODO: Move get workflow ID logic into panel to ensure order of hooks called
  const { mutate } = useUpdateWorkflow(workflowId);
  function onSubmit(values: z.infer<typeof workflowFormSchema>) {
    mutate(values);
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-4 p-4">
          <div className="space-y-3">
            <h4 className="text-sm font-medium">Workflow Status</h4>
            <div className="flex justify-between">
              <Badge variant="outline" className={`py-1 px-4 ${workflowStatus === "online" ? 'bg-green-100' : 'bg-gray-100'}`}>
                <CircleIcon className={`mr-2 h-3 w-3 ${workflowStatus === "online" ? 'fill-green-600 text-green-600' : 'fill-gray-400 text-gray-400'}`} />
                <span className={`text-muted-foreground ${workflowStatus === "online" ? 'text-green-600' : 'text-gray-600'}`}>{statusCapitalized}</span>
              </Badge>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="submit" size="icon">
                    <Save className="h-4 w-4" />
                    <span className="sr-only">Save</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Save</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <Separator />
          <div className="space-y-4">
            <FormField
              control={form.control}
              name="title"
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
          </div>
        </div>
      </form>
    </Form>
  )
}
