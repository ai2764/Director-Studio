import { PageShell } from "../../shared/components/PageShell";
import { H3WorkflowSetup } from "./H3WorkflowSetup";

export function WorkflowSettingsPage() {
  return (
    <PageShell
      title="Settings"
      subtitle="Workflows / H3"
      className="workflow-settings-page"
    >
      <H3WorkflowSetup />
    </PageShell>
  );
}
