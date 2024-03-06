"use client"

import React, { createContext, ReactNode, useContext, useState } from "react"

export type WorkflowMetadata = {
  id: string | undefined
  title: string | undefined
  description: string | undefined
  status: string | undefined
}

type SelectedWorkflowContextType = {
  selectedWorkflowMetadata: WorkflowMetadata
  setSelectedWorkflowMetadata: (workflow: WorkflowMetadata) => void
}

const SelectedWorkflowContext = createContext<
  SelectedWorkflowContextType | undefined
>(undefined)

export const SelectedWorkflowProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const [selectedWorkflowMetadata, setSelectedWorkflowMetadata] =
    useState<WorkflowMetadata>({
      id: undefined,
      title: undefined,
      description: undefined,
      status: undefined,
    })

  return (
    <SelectedWorkflowContext.Provider
      value={{ selectedWorkflowMetadata, setSelectedWorkflowMetadata }}
    >
      {children}
    </SelectedWorkflowContext.Provider>
  )
}

export const useSelectedWorkflowMetadata = () => {
  const context = useContext(SelectedWorkflowContext)
  if (context === undefined) {
    throw new Error(
      "useSelectedWorkflow must be used within a SelectedWorkflowProvider"
    )
  }
  return context
}
