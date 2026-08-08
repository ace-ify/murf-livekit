'use client';

import { SamarDashboard } from '@/components/app/samar-dashboard';

// ViewController now renders the full Samar clinical dashboard.
// The old WelcomeView / AgentSessionView_01 split is replaced by
// SamarDashboard which handles all states internally.
export function ViewController() {
  return <SamarDashboard />;
}
