export interface AppConfig {
  pageTitle: string;
  pageDescription: string;
  companyName: string;

  supportsChatInput: boolean;

  logo: string;
  startButtonText: string;
  accent?: string;
  logoDark?: string;
  accentDark?: string;

  // agent dispatch configuration
  agentName?: string;

  // LiveKit Cloud Sandbox configuration
  sandboxId?: string;
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'Careva Health Helpline',
  pageTitle: 'Careva — AI Health Access Platform',
  pageDescription: 'A voice helpline for health centres, powered by Murf Falcon and LiveKit Agents',

  // Voice-only on purpose: this models a phone call to a health centre.
  supportsChatInput: true,

  logo: '/careva.png',
  accent: '#0D9488',
  logoDark: '/careva.png',
  accentDark: '#2DD4BF',
  startButtonText: 'Call Careva',

  // agent dispatch configuration
  agentName: process.env.AGENT_NAME ?? undefined,

  // LiveKit Cloud Sandbox configuration
  sandboxId: undefined,
};
