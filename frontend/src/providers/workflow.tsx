"use client"

import React, { createContext, ReactNode, useContext, useState } from "react"

export type WorkflowMetadata = {
  id: string | undefined
  title: string | undefined
  description: string | undefined
  status: string | undefined
}

type WorkflowContextType = {
  workflowMetadata: WorkflowMetadata
  setWorkflowMetadata: (workflow: WorkflowMetadata) => void
}

const WorkflowContext = createContext<WorkflowContextType | undefined>(
  undefined
)

export const WorkflowProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const [workflowMetadata, setWorkflowMetadata] = useState<WorkflowMetadata>({
    id: undefined,
    title: undefined,
    description: undefined,
    status: undefined,
  })

  return (
    <WorkflowContext.Provider value={{ workflowMetadata, setWorkflowMetadata }}>
      {children}
    </WorkflowContext.Provider>
  )
}

export const useWorkflowMetadata = () => {
  const context = useContext(WorkflowContext)
  if (context === undefined) {
    throw new Error("useWorkflow must be used within a WorkflowProvider")
  }
  return context
}
