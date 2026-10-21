/** Cross-page "open Settings on tab X" requests (doctor / wizard → Workflow dependencies). */
export const OPEN_SETTINGS_EVENT = "ds:open-settings";
let pending: string | null = null;

export function requestSettingsTab(tab: string): void {
  pending = tab;
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: { tab } }));
}

/** Read (and clear) a request made before the settings page was mounted. */
export function consumeSettingsTab(): string | null {
  const tab = pending;
  pending = null;
  return tab;
}
