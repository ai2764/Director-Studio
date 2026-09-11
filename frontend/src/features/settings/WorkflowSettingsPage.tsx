import { PageShell } from "../../shared/components/PageShell";
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
      className="workflow-settings-page"
      actions={onClose ? (
        <button
          type="button"
          className="settings-close-button"
          aria-label="Close settings"
          title="Close settings"
          onClick={onClose}
        >
          ×
        </button>
      ) : null}
    >
      <H3WorkflowSetup active={active} />
    </PageShell>
  );
}
