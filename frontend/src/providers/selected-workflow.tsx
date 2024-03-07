"use client"

import React, { createContext, ReactNode, useContext, useState } from "react"

export type WorkflowMetadata = {
  id?: string
  title?: string
  description?: string
  status?: string
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
    useState<WorkflowMetadata>({})

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
