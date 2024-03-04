"use client"

import React, { createContext, useContext, useState, ReactNode } from 'react';

type SelectedWorkflowContextType = {
  selectedWorkflowId: string | undefined;
  setSelectedWorkflowId: (id: string | undefined) => void;
};

const SelectedWorkflowContext = createContext<SelectedWorkflowContextType | undefined>(undefined);

export const SelectedWorkflowProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | undefined>(undefined);
  return (
    <SelectedWorkflowContext.Provider value={{ selectedWorkflowId, setSelectedWorkflowId }}>
      {children}
    </SelectedWorkflowContext.Provider>
  );
};

export const useSelectedWorkflow = () => {
  const context = useContext(SelectedWorkflowContext);
  if (context === undefined) {
    throw new Error("useSelectedWorkflow must be used within a SelectedWorkflowProvider");
  }
  return context;
};
