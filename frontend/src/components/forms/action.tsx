import React, { useEffect, useState } from "react";

import axios from "axios";
import { useQuery, useMutation } from "@tanstack/react-query";
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
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"

import { CircleIcon, Save } from "lucide-react"

// Define formSchema for type safety
const actionFormSchema = z.object({
  name: z.string(),
  description: z.string(),
  inputs: z.string().optional(),
})

interface ActionResponse {
  id: string;
  title: string;
  description: string;
  status: string;
  inputs: Record<string, any> | null;
}

interface ActionFormProps {
  actionId: string;
}

export function ActionForm({ actionId }: ActionFormProps): React.JSX.Element {

  const { selectedWorkflowMetadata, setSelectedWorkflowMetadata } = useSelectedWorkflowMetadata();
  const workflowId = selectedWorkflowMetadata.id;

  // Fetch Action by ID and Workflow ID
  const getActionById = async (): Promise<ActionResponse> => {
    try {
      const response = await axios.get<ActionResponse>(`http://localhost:8000/actions/${actionId}?workflow_id=${workflowId}`);
      return response.data;
    } catch (error) {
      console.error("Error fetching action:", error);
      throw error; // Rethrow the error to ensure it's caught by useQuery's isError state
    }
  };

  const { data, isLoading, isError } = useQuery<ActionResponse, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: getActionById,
  });

  const [status, setStatus] = useState("offline");
  const form = useForm<z.infer<typeof actionFormSchema>>({
    resolver: zodResolver(actionFormSchema),
    defaultValues: {
      name: "", // Default value for name
      description: "", // Default value for description
    },
  });

  useEffect(() => {
    if (data) {
      const { title, description, status } = data;
      form.reset({ // Use reset method to set form values
        name: title,
        description: description,
      });
      setStatus(status);
    }
  }, [data, form.reset]);
  const statusCapitalized = status[0].toUpperCase() + status.slice(1);

  // Submit form and update Action
  async function updateAction(actionId: string, values: z.infer<typeof actionFormSchema>) {
    const response = await axios.post(`http://localhost:8000/actions/${actionId}`, values);
    return response.data; // Adjust based on what your API returns
  }

  function useUpdateAction(actionId: string) {
    const mutation = useMutation({
      mutationFn: (values: z.infer<typeof actionFormSchema>) => updateAction(actionId, values),
      // Configure your mutation behavior here
      onSuccess: (data, variables, context) => {
        console.log("Action update successful", data);
      },
      onError: (error, variables, context) => {
        console.error("Failed to update action:", error);
      },
    });

    return mutation;
  }

  const { mutate } = useUpdateAction(actionId);
  function onSubmit(values: z.infer<typeof actionFormSchema>) {
    mutate(values);
  }

  if (!data) {
    return (
      <div className="flex items-center space-x-2 p-4">
        <div className="space-y-2">
          <Skeleton className="h-4 w-[250px]" />
          <Skeleton className="h-4 w-[200px]" />
        </div>
      </div>
    )
  }

  return (
    <ScrollArea>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)}>
          <div className="space-y-4 p-4">
            <div className="space-y-3">
              <h4 className="text-sm font-medium">Action Status</h4>
              <div className="flex justify-between">
                <Badge variant="outline" className={`py-1 px-4 ${status === "online" ? 'bg-green-100' : 'bg-gray-100'}`}>
                  <CircleIcon className={`mr-2 h-3 w-3 ${status === "online" ? 'fill-green-600 text-green-600' : 'fill-gray-400 text-gray-400'}`} />
                  <span className={`text-muted-foreground ${status === "online" ? 'text-green-600' : 'text-gray-600'}`}>{statusCapitalized}</span>
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
          </div>
        </form>
      </Form>
    </ScrollArea>
  )
}
