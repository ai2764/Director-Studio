import { X } from "lucide-react";
import { PageShell } from "../../shared/components/PageShell";
import { Button } from "../../shared/ui";
import { H3WorkflowSetup } from "./H3WorkflowSetup";

export function WorkflowSettingsPage({
  active = true,
  onClose,
}: {
  active?: boolean;
  onClose?: () => void;
}) {
  return (
    <PageShell
      title="Settings"
      subtitle="Workflows / H3"
      width="wide"
      className="workflow-settings-page"
      actions={
        onClose ? (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="settings-close-button"
            aria-label="Close settings"
            title="Close settings"
            onClick={onClose}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        ) : null
      }
    >
      <H3WorkflowSetup active={active} />
    </PageShell>
  );
}
