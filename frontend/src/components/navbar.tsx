"use client";

import { useEffect, useState } from "react"
import axios from "axios"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { UserNav } from "@/components/user-nav"
import WorkflowSwitcher from "@/components/workflow-switcher"
import { useSelectedWorkflowMetadata } from "@/providers/selected-workflow"

import {
  WorkflowIcon,
  BellRingIcon,
} from "lucide-react"


export function Navbar() {

  const { selectedWorkflowMetadata } = useSelectedWorkflowMetadata(); // This assumes the existence of such a hook
  const [enableWorkflow, setEnableWorkflow] = useState(false);
  const selectedWorkflowId = selectedWorkflowMetadata.id;

  useEffect(() => {
    const updateWorkflowStatus = async () => {
      if (selectedWorkflowMetadata && selectedWorkflowId) {
        const status = enableWorkflow ? "online" : "offline";
        try {
          await axios.post(`http://localhost:8000/workflows/${selectedWorkflowId}`, JSON.stringify({
            status: status,
          }), {
            headers: {
              "Content-Type": "application/json",
            },
          });
          console.log(`Workflow ${selectedWorkflowId} set to ${status}`);
        } catch (error) {
          console.error("Failed to update workflow status:", error);
        }
      }
    };

    updateWorkflowStatus();
  }, [enableWorkflow, selectedWorkflowMetadata]);

  return (
    <div className="border-b">
      <div className="flex h-16 items-center px-4">
        <div className="flex space-x-8">
          <WorkflowSwitcher />
          <Tabs defaultValue="workspace-view">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="workflow">
                <WorkflowIcon className="h-4 w-4 mr-2" />
                Workflow
              </TabsTrigger>
              <TabsTrigger value="cases">
                <BellRingIcon className="h-4 w-4 mr-2" />
                Cases
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <div className="ml-auto flex items-center space-x-8">
          <div className="flex items-center space-x-2">
            <Switch
              id="enable-workflow"
              checked={enableWorkflow}
              onCheckedChange={(newCheckedState) => setEnableWorkflow(newCheckedState)}
            />
            <Label className="w-32" htmlFor="enable-workflow">{enableWorkflow ? "Disable workflow" : "Enable workflow"}</Label>
          </div>
          <UserNav />
        </div>
      </div>
    </div>
  )
}
