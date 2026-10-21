import { SettingsPage, type SettingsTab } from "./SettingsPage";

/**
 * Settings container. Kept under its historical name so existing imports keep
 * working; the H3 workflow setup now lives in the "工作流 / Workflows" tab.
 */
export function WorkflowSettingsPage({
  active = true,
  onClose,
  onRunWizard,
  initialTab,
}: {
  active?: boolean;
  onClose?: () => void;
  onRunWizard?: () => void;
  initialTab?: SettingsTab;
}) {
  return <SettingsPage active={active} onClose={onClose} onRunWizard={onRunWizard} initialTab={initialTab} />;
}
