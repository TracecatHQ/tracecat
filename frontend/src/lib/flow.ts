import axios from 'axios';
import { ReactFlowInstance } from 'reactflow';


export async function saveFlow(workflowId: string | undefined, reactFlowInstance: ReactFlowInstance | null) {
  if (!workflowId || !reactFlowInstance) return;

  try {
    const flowObject = reactFlowInstance.toObject();
    const updateFlowObjectParams = JSON.stringify({ object: JSON.stringify(flowObject) });
    await axios.post(`http://localhost:8000/workflows/${workflowId}`, updateFlowObjectParams, {
      headers: {
        "Content-Type": "application/json",
      },
    });

    console.log("Flow saved successfully");
  } catch (error) {
    console.error("Error saving flow:", error);
  }
}
