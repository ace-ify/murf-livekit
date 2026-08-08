'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  useAgent,
  useSessionContext,
  useSessionMessages,
  useVoiceAssistant,
} from '@livekit/components-react';
import {
  Activity,
  AlertCircle,
  Clock,
  HeartPulse,
  History,
  Home,
  Info,
  Lock,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Radio,
  RotateCcw,
  Settings,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  User,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { AgentAudioVisualizerAura } from '@/components/agents-ui/agent-audio-visualizer-aura';
import { AgentChatTranscript } from '@/components/agents-ui/agent-chat-transcript';
import { useInputControls } from '@/hooks/agents-ui/use-agent-control-bar';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/shadcn/utils';

// ── visual tokens ─────────────────────────────────────────────────────────────
const AURA_PRIMARY_COLOR = '#8b5cf6' as const; // rich violet
const AURA_COLOR_SHIFT = 0.32; // shifts smoothly between violet and teal/cyan

export type DashState =
  | 'ready'
  | 'connecting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'ended';

interface StateMeta {
  label: string;
  sublabel: string;
  hindiLabel: string;
  dotColor: string;
  glowClass: string;
  badgeBg: string;
  badgeText: string;
  badgeBorder: string;
}

const STATE_CONFIG: Record<DashState, StateMeta> = {
  ready: {
    label: 'Ready to Connect',
    sublabel: 'One-tap voice connection to Samar',
    hindiLabel: 'कॉल शुरू करने के लिए तैयार',
    dotColor: 'bg-slate-400',
    glowClass: 'shadow-[0_0_90px_30px_rgba(139,92,246,0.12)]',
    badgeBg: 'bg-slate-800/80',
    badgeText: 'text-slate-300',
    badgeBorder: 'border-slate-700/60',
  },
  connecting: {
    label: 'Connecting to PHC Helpline…',
    sublabel: 'Establishing secure low-latency voice pipeline',
    hindiLabel: 'हेल्पलाइन से जुड़ रहा है…',
    dotColor: 'bg-amber-400 animate-ping',
    glowClass: 'shadow-[0_0_120px_45px_rgba(245,158,11,0.18)]',
    badgeBg: 'bg-amber-950/60',
    badgeText: 'text-amber-300',
    badgeBorder: 'border-amber-500/40',
  },
  listening: {
    label: 'Listening to you',
    sublabel: 'Speak naturally in Hindi, Hinglish, or English',
    hindiLabel: 'आपकी बात सुन रहे हैं… बोलिए',
    dotColor: 'bg-emerald-400 animate-pulse',
    glowClass: 'shadow-[0_0_140px_50px_rgba(16,185,129,0.22)]',
    badgeBg: 'bg-emerald-950/60',
    badgeText: 'text-emerald-300',
    badgeBorder: 'border-emerald-500/40',
  },
  thinking: {
    label: 'Samar is thinking…',
    sublabel: 'Evaluating clinical guardrails & triage rules',
    hindiLabel: 'जानकारी की जांच हो रही है…',
    dotColor: 'bg-sky-400 animate-pulse',
    glowClass: 'shadow-[0_0_130px_45px_rgba(56,189,248,0.2)]',
    badgeBg: 'bg-sky-950/60',
    badgeText: 'text-sky-300',
    badgeBorder: 'border-sky-500/40',
  },
  speaking: {
    label: 'Samar is speaking',
    sublabel: 'Streaming response powered by Murf Falcon TTS',
    hindiLabel: 'समर बोल रहा है…',
    dotColor: 'bg-violet-400 animate-pulse',
    glowClass: 'shadow-[0_0_160px_60px_rgba(139,92,246,0.28)]',
    badgeBg: 'bg-violet-950/60',
    badgeText: 'text-violet-300',
    badgeBorder: 'border-violet-500/40',
  },
  ended: {
    label: 'Call Ended',
    sublabel: 'Session completed. You can start a new call anytime.',
    hindiLabel: 'कॉल समाप्त हो गई है',
    dotColor: 'bg-rose-400',
    glowClass: 'shadow-[0_0_80px_25px_rgba(244,63,94,0.12)]',
    badgeBg: 'bg-rose-950/60',
    badgeText: 'text-rose-300',
    badgeBorder: 'border-rose-500/40',
  },
};

// ── microphone permissions hook ───────────────────────────────────────────────
function useMicStatus() {
  const [isDenied, setIsDenied] = useState(false);

  useEffect(() => {
    if (!navigator?.permissions) return;
    let permissionStatus: PermissionStatus | null = null;

    navigator.permissions
      .query({ name: 'microphone' as PermissionName })
      .then((status) => {
        permissionStatus = status;
        setIsDenied(status.state === 'denied');
        status.onchange = () => {
          setIsDenied(status.state === 'denied');
        };
      })
      .catch(() => {});

    return () => {
      if (permissionStatus) {
        permissionStatus.onchange = null;
      }
    };
  }, []);

  return { isDenied, setIsDenied };
}

// ── session duration timer ────────────────────────────────────────────────────
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

export function SamarDashboard() {
  const { isConnected, start, end } = useSessionContext();
  const { state: vaState, audioTrack } = useVoiceAssistant();
  const { state: agentState } = useAgent();
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const { isDenied } = useMicStatus();
  const { microphoneToggle } = useInputControls();

  const [hasStartedOnce, setHasStartedOnce] = useState(false);
  const [sessionSeconds, setSessionSeconds] = useState(0);
  const [activeTab, setActiveTab] = useState<'dashboard' | 'transcript' | 'info'>('dashboard');

  // Track session timer
  useEffect(() => {
    let interval: NodeJS.Timeout | null = null;
    if (isConnected) {
      setHasStartedOnce(true);
      interval = setInterval(() => {
        setSessionSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      if (!hasStartedOnce) {
        setSessionSeconds(0);
      }
    }
    return () => {
      if (interval) clearInterval(interval);
    };
  }, [isConnected, hasStartedOnce]);

  // Compute cohesive dashboard state
  const dashState = useMemo<DashState>(() => {
    if (!isConnected) {
      return hasStartedOnce ? 'ended' : 'ready';
    }
    const stateString = (agentState ?? vaState ?? '').toString();
    switch (stateString) {
      case 'connecting':
      case 'initializing':
        return 'connecting';
      case 'thinking':
        return 'thinking';
      case 'speaking':
        return 'speaking';
      case 'listening':
        return 'listening';
      default:
        return 'listening';
    }
  }, [isConnected, hasStartedOnce, agentState, vaState]);

  const stateMeta = STATE_CONFIG[dashState];

  return (
    <div className="relative flex h-screen w-full overflow-hidden bg-[#070511] font-sans text-slate-100 antialiased selection:bg-violet-500/30">
      {/* Background ambient radial glow */}
      <div
        className="pointer-events-none absolute -top-40 left-1/2 h-[600px] w-[900px] -translate-x-1/2 rounded-full bg-violet-900/15 blur-[140px] transition-all duration-1000"
        style={{
          background:
            dashState === 'speaking'
              ? 'radial-gradient(circle, rgba(139,92,246,0.22) 0%, rgba(13,148,136,0.08) 50%, transparent 80%)'
              : dashState === 'listening'
                ? 'radial-gradient(circle, rgba(16,185,129,0.18) 0%, rgba(139,92,246,0.08) 50%, transparent 80%)'
                : dashState === 'connecting'
                  ? 'radial-gradient(circle, rgba(245,158,11,0.18) 0%, transparent 70%)'
                  : 'radial-gradient(circle, rgba(139,92,246,0.14) 0%, transparent 70%)',
        }}
      />

      {/* ── Left Sidebar (Icon + Nav Rail) ─────────────────────────────────── */}
      <aside className="relative z-20 flex w-16 shrink-0 flex-col items-center justify-between border-r border-white/[0.06] bg-[#0a071c]/90 py-4 backdrop-blur-md md:w-56 md:items-stretch md:px-3">
        {/* Brand Header */}
        <div>
          <div className="flex items-center gap-3 px-2 py-2">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-teal-500 shadow-md shadow-violet-600/30">
              <Stethoscope className="size-5 text-white" />
            </div>
            <div className="hidden flex-col md:flex">
              <div className="flex items-center gap-1.5">
                <span className="text-base font-bold tracking-tight text-white">
                  Samar
                </span>
                <span className="rounded bg-teal-500/20 px-1.5 py-0.5 text-[9px] font-semibold text-teal-300">
                  PHC
                </span>
              </div>
              <span className="text-[11px] text-slate-400">Health Access Helpline</span>
            </div>
          </div>

          {/* Nav items */}
          <nav className="mt-6 flex flex-col gap-1">
            <button
              onClick={() => setActiveTab('dashboard')}
              className={cn(
                'group relative flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-sm font-medium transition-all',
                activeTab === 'dashboard'
                  ? 'bg-violet-600/15 text-violet-300 shadow-inner'
                  : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
              )}
            >
              <Home className="size-4 shrink-0" />
              <span className="hidden md:inline">Helpline Console</span>
              {activeTab === 'dashboard' && (
                <motion.div
                  layoutId="active-nav-indicator"
                  className="absolute left-0 top-1/2 hidden h-5 w-1 -translate-y-1/2 rounded-r-full bg-violet-400 md:block"
                />
              )}
            </button>

            <button
              onClick={() => setActiveTab('transcript')}
              className={cn(
                'group relative flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-sm font-medium transition-all',
                activeTab === 'transcript'
                  ? 'bg-violet-600/15 text-violet-300 shadow-inner'
                  : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-200'
              )}
            >
              <Activity className="size-4 shrink-0" />
              <span className="hidden md:inline">Live Triage Log</span>
              {messages.length > 0 && (
                <span className="ml-auto hidden rounded-full bg-violet-500/30 px-1.5 py-0.2 text-[10px] text-violet-300 md:inline">
                  {messages.length}
                </span>
              )}
            </button>

            <div className="my-2 border-t border-white/[0.06]" />

            {/* Future Scope Items (Clearly marked) */}
            <div className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-xs font-normal text-slate-600 opacity-60">
              <History className="size-4 shrink-0" />
              <span className="hidden md:inline">Call Records</span>
              <Lock className="ml-auto hidden size-3 md:inline" />
            </div>

            <div className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-xs font-normal text-slate-600 opacity-60">
              <ShieldCheck className="size-4 shrink-0" />
              <span className="hidden md:inline">PHC Directory</span>
              <Lock className="ml-auto hidden size-3 md:inline" />
            </div>

            <div className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-xs font-normal text-slate-600 opacity-60">
              <Settings className="size-4 shrink-0" />
              <span className="hidden md:inline">Settings</span>
              <Lock className="ml-auto hidden size-3 md:inline" />
            </div>
          </nav>
        </div>

        {/* Sidebar Footer (LiveKit + Murf Falcon info) */}
        <div className="flex flex-col gap-3">
          <div className="hidden rounded-xl border border-white/[0.06] bg-white/[0.02] p-2.5 md:block">
            <div className="flex items-center gap-2">
              <Radio className="size-3.5 text-teal-400" />
              <span className="text-[11px] font-semibold text-slate-200">
                Murf Falcon TTS
              </span>
            </div>
            <p className="mt-1 text-[10px] text-slate-400">
              Ultra-low latency streaming voice in Hindi & English
            </p>
          </div>

          <div className="flex items-center gap-2.5 px-2 py-1">
            <div className="flex size-7 items-center justify-center rounded-full bg-slate-800 text-slate-300">
              <User className="size-3.5" />
            </div>
            <div className="hidden flex-col md:flex">
              <span className="text-xs font-medium text-slate-200">
                PHC Duty Officer
              </span>
              <span className="text-[10px] text-teal-400">#VoiceForBharat</span>
            </div>
          </div>
        </div>
      </aside>

      {/* ── Main Workspace ─────────────────────────────────────────────────── */}
      <div className="relative z-10 flex flex-1 flex-col overflow-hidden">
        {/* Top Header Bar */}
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#070511]/80 px-4 backdrop-blur-md md:px-6">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                'flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium backdrop-blur-md transition-colors',
                stateMeta.badgeBg,
                stateMeta.badgeText,
                stateMeta.badgeBorder
              )}
            >
              <span className={cn('size-2 rounded-full', stateMeta.dotColor)} />
              <span>{stateMeta.label}</span>
              <span className="hidden opacity-60 md:inline">|</span>
              <span className="hidden opacity-80 md:inline">{stateMeta.hindiLabel}</span>
            </div>
          </div>

          {/* Right Action Bar & Session Timer */}
          <div className="flex items-center gap-3">
            {isConnected && (
              <div className="flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-xs text-slate-300">
                <Clock className="size-3.5 text-violet-400" />
                <span className="font-mono">{formatDuration(sessionSeconds)}</span>
              </div>
            )}

            {/* Quick Call / Disconnect CTA */}
            {!isConnected ? (
              <Button
                onClick={() => start()}
                className="group flex items-center gap-2 rounded-xl bg-gradient-to-r from-violet-600 to-teal-600 px-4 py-2 text-xs font-semibold text-white shadow-lg shadow-violet-600/30 transition-all hover:scale-105 hover:from-violet-500 hover:to-teal-500"
              >
                <Phone className="size-3.5 transition-transform group-hover:rotate-12" />
                <span>Call Samar</span>
                <span className="hidden text-[10px] opacity-80 sm:inline">(कॉल करें)</span>
              </Button>
            ) : (
              <Button
                onClick={() => end()}
                variant="destructive"
                className="flex items-center gap-2 rounded-xl bg-rose-600/90 px-4 py-2 text-xs font-semibold text-white shadow-lg shadow-rose-600/30 hover:bg-rose-600"
              >
                <PhoneOff className="size-3.5" />
                <span>End Call</span>
                <span className="hidden text-[10px] opacity-80 sm:inline">(समाप्त)</span>
              </Button>
            )}
          </div>
        </header>

        {/* ── Microphone Error Banner (if blocked) ──────────────────────────── */}
        <AnimatePresence>
          {isDenied && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="border-b border-rose-500/30 bg-rose-950/70 px-4 py-2.5 backdrop-blur-md"
            >
              <div className="mx-auto flex max-w-4xl items-center justify-between gap-3 text-xs text-rose-200">
                <div className="flex items-center gap-2">
                  <AlertCircle className="size-4 shrink-0 text-rose-400" />
                  <span>
                    <strong>Microphone access is blocked.</strong> Samar needs microphone permission to hear your voice. Please enable microphone permissions in your browser URL bar.
                  </span>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    navigator.mediaDevices
                      ?.getUserMedia({ audio: true })
                      .catch(() => {});
                  }}
                  className="shrink-0 border-rose-500/40 text-xs text-rose-200 hover:bg-rose-900/50"
                >
                  Retry Mic Access
                </Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Main Interactive Canvas ────────────────────────────────────────── */}
        <main className="flex flex-1 flex-col overflow-y-auto p-4 md:flex-row md:gap-4 md:p-6">
          {/* Center / Left: Aurora Visualizer Stage */}
          <div className="relative flex flex-1 flex-col items-center justify-between rounded-2xl border border-white/[0.08] bg-gradient-to-b from-white/[0.04] to-white/[0.01] p-6 shadow-2xl backdrop-blur-xl">
            {/* Top Stage Header: Speaker & Channel Status */}
            <div className="flex w-full items-center justify-between">
              <div className="flex items-center gap-2">
                <HeartPulse className="size-4 text-violet-400" />
                <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Rural Health Voice Pipeline
                </span>
              </div>
              <div className="flex items-center gap-2 rounded-full border border-white/[0.06] bg-white/[0.03] px-2.5 py-1 text-[11px] text-slate-300">
                <Sparkles className="size-3 text-teal-400" />
                <span>Hi-IN / Hinglish / English</span>
              </div>
            </div>

            {/* Center: Hero Aurora Waveform Orb */}
            <div className="relative my-auto flex flex-col items-center justify-center py-6">
              {/* Dynamic Aura Glow Ring */}
              <div
                className={cn(
                  'relative flex items-center justify-center rounded-full transition-all duration-700',
                  stateMeta.glowClass
                )}
              >
                <AgentAudioVisualizerAura
                  size="xl"
                  state={
                    dashState === 'ready'
                      ? 'disconnected'
                      : dashState === 'ended'
                        ? 'disconnected'
                        : (agentState ?? 'listening')
                  }
                  color={
                    dashState === 'listening'
                      ? '#10b981' // emerald when user speaks
                      : dashState === 'connecting'
                        ? '#f59e0b' // amber when connecting
                        : AURA_PRIMARY_COLOR
                  }
                  colorShift={AURA_COLOR_SHIFT}
                  audioTrack={audioTrack}
                  themeMode="dark"
                  className="size-[250px] sm:size-[300px] md:size-[360px]"
                />

                {/* Overlay CTA when ready or ended */}
                {!isConnected && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center rounded-full bg-[#070511]/40 backdrop-blur-[2px]">
                    <motion.div
                      initial={{ scale: 0.9, opacity: 0 }}
                      animate={{ scale: 1, opacity: 1 }}
                      className="flex flex-col items-center gap-3 text-center"
                    >
                      <button
                        onClick={() => start()}
                        className="group flex size-20 items-center justify-center rounded-full bg-gradient-to-tr from-violet-600 to-teal-500 text-white shadow-xl shadow-violet-600/40 transition-all hover:scale-110 active:scale-95"
                      >
                        {hasStartedOnce ? (
                          <RotateCcw className="size-8 transition-transform group-hover:rotate-180" />
                        ) : (
                          <Phone className="size-8 transition-transform group-hover:scale-110" />
                        )}
                      </button>
                      <span className="text-xs font-semibold text-slate-200">
                        {hasStartedOnce ? 'Call Again (पुनः कॉल)' : 'Tap to Start Call'}
                      </span>
                    </motion.div>
                  </div>
                )}
              </div>

              {/* Sub-Aura Status Badge */}
              <div className="mt-4 flex flex-col items-center gap-1">
                <div className="flex items-center gap-2">
                  <span className={cn('size-2.5 rounded-full', stateMeta.dotColor)} />
                  <span className="text-sm font-semibold text-slate-100">
                    {stateMeta.label}
                  </span>
                </div>
                <span className="text-xs text-slate-400">{stateMeta.sublabel}</span>
              </div>
            </div>

            {/* Bottom Controls Bar (LiveKit Track Controls) */}
            <div className="flex w-full flex-col items-center justify-between gap-3 border-t border-white/[0.06] pt-4 sm:flex-row">
              {/* Mic Toggle & Audio Indicator */}
              <div className="flex items-center gap-2">
                {isConnected && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => microphoneToggle.toggle()}
                    className={cn(
                      'flex items-center gap-2 rounded-xl border text-xs font-medium transition-all',
                      microphoneToggle.enabled
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300 hover:bg-emerald-500/20'
                        : 'border-rose-500/40 bg-rose-500/10 text-rose-300 hover:bg-rose-500/20'
                    )}
                  >
                    {microphoneToggle.enabled ? (
                      <>
                        <Mic className="size-3.5" />
                        <span>Mute Mic</span>
                      </>
                    ) : (
                      <>
                        <MicOff className="size-3.5" />
                        <span>Unmute Mic</span>
                      </>
                    )}
                  </Button>
                )}

                <div className="flex items-center gap-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-1.5 text-xs text-slate-400">
                  <Info className="size-3 text-teal-400" />
                  <span>Speech-to-Text: Deepgram Nova-3</span>
                </div>
              </div>

              {/* Suggested Triage Prompts */}
              {!isConnected && (
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400">
                  <span className="text-slate-500">Ask:</span>
                  <span className="rounded-md bg-white/[0.04] px-2 py-0.5 text-slate-300">
                    "Mere bete ko bukhar hai"
                  </span>
                  <span className="rounded-md bg-white/[0.04] px-2 py-0.5 text-slate-300">
                    "Vaccine schedule for newborn"
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* Right Column: Live Transcript & Clinical Context Card */}
          <div className="mt-4 flex w-full flex-col gap-4 md:mt-0 md:w-80 lg:w-96">
            {/* Live Triage Log Panel */}
            <div className="flex h-[360px] flex-1 flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03] backdrop-blur-xl">
              <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
                <div className="flex items-center gap-2">
                  <Activity className="size-4 text-teal-400" />
                  <span className="text-xs font-semibold text-slate-200">
                    Live Conversation Transcript
                  </span>
                </div>
                <span className="rounded bg-teal-500/10 px-1.5 py-0.5 text-[10px] font-medium text-teal-300">
                  Real-time
                </span>
              </div>

              {/* Chat Transcript Area */}
              <div className="flex-1 overflow-y-auto p-3">
                {messages.length === 0 ? (
                  <div className="flex h-full flex-col items-center justify-center p-6 text-center text-slate-500">
                    <Activity className="mb-2 size-8 text-slate-600 opacity-40" />
                    <p className="text-xs font-medium text-slate-400">
                      No speech recorded yet
                    </p>
                    <p className="mt-1 text-[11px] text-slate-600">
                      Start speaking when connected. The live transcript in Hindi & English will appear here.
                    </p>
                  </div>
                ) : (
                  <AgentChatTranscript
                    agentState={agentState}
                    messages={messages}
                    className="w-full text-xs"
                  />
                )}
              </div>
            </div>

            {/* Clinical Guardrails & Engine Summary Card */}
            <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4 text-xs backdrop-blur-xl">
              <div className="flex items-center gap-2 text-slate-300">
                <ShieldCheck className="size-4 text-teal-400" />
                <span className="font-semibold">Guardrails & Track Specs</span>
              </div>
              <ul className="mt-2.5 space-y-1.5 text-[11px] text-slate-400">
                <li className="flex items-start gap-1.5">
                  <span className="font-bold text-teal-400">✓</span>
                  <span><strong>108 Emergency:</strong> Immediate triage escalation</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="font-bold text-teal-400">✓</span>
                  <span><strong>No Dosages:</strong> Doctor-referred medicine advice</span>
                </li>
                <li className="flex items-start gap-1.5">
                  <span className="font-bold text-teal-400">✓</span>
                  <span><strong>TTS:</strong> Murf Falcon streaming voice</span>
                </li>
              </ul>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
