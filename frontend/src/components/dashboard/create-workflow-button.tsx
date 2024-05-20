"use client"

import { useRouter } from "next/navigation";
import { PlusCircleIcon } from "lucide-react";

import { createWorkflow } from "@/lib/flow";
import { Button } from "@/components/ui/button";

const CreateWorkflowButton: React.FC = () => {
    const router = useRouter();

    const handleCreateWorkflow = async () => {
        try {
            // Get the current date and format it
            const currentDate = new Date();
            const formattedDate = currentDate.toLocaleString('en-US', {
                month: 'short',
                day: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
            });
            const title = "New workflow created";
            const description = `New workflow created ${formattedDate}.`;

            const response = await createWorkflow(title, description);

            // Redirect to the new workflow's page
            router.push(`/workflows/${response.id}`);
        } catch (error) {
            console.error("Error adding workflow:", error);
        }
    };

    return (
        <Button
            variant="outline"
            role="combobox"
            className="space-x-2"
            onClick={handleCreateWorkflow}
        >
            <PlusCircleIcon className="h-4 w-4" />
            <span>New workflow</span>
        </Button>
    );
};

export default CreateWorkflowButton;
