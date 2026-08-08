'use client';

import React, { useEffect, useMemo, useState } from 'react';
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
  BarChart2,
  CheckCircle2,
  Clock,
  FileText,
  Folder,
  History,
  Lock,
  Mic,
  MicOff,
  Phone,
  PhoneOff,
  Radio,
  RotateCcw,
  Settings,
  Shield,
  Sparkles,
  Stethoscope,
  Volume2,
} from 'lucide-react';
import { AgentAudioVisualizerWave } from '@/components/agents-ui/agent-audio-visualizer-wave';
import { useInputControls } from '@/hooks/agents-ui/use-agent-control-bar';
import { cn } from '@/lib/shadcn/utils';

export type DashState =
  | 'ready'
  | 'connecting'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'ended';

interface StateMeta {
  badgeText: string;
  hindiText: string;
  dotBg: string;
  pillBg: string;
  pillBorder: string;
  textColor: string;
}

const STATE_CONFIG: Record<DashState, StateMeta> = {
  ready: {
    badgeText: 'AGENT READY',
    hindiText: 'कॉल शुरू करने के लिए तैयार',
    dotBg: 'bg-slate-400',
    pillBg: 'bg-white',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
  connecting: {
    badgeText: 'CONNECTING...',
    hindiText: 'हेल्पलाइन से जुड़ रहा है...',
    dotBg: 'bg-amber-400 animate-ping',
    pillBg: 'bg-amber-100',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
  listening: {
    badgeText: 'AGENT LISTENING',
    hindiText: 'आपकी बात सुन रहे हैं...',
    dotBg: 'bg-emerald-400 animate-pulse',
    pillBg: 'bg-emerald-100',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
  thinking: {
    badgeText: 'EVALUATING TRIAGE',
    hindiText: 'जांच हो रही है...',
    dotBg: 'bg-sky-400 animate-pulse',
    pillBg: 'bg-sky-100',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
  speaking: {
    badgeText: 'SAMAR SPEAKING',
    hindiText: 'समर बोल रहा है...',
    dotBg: 'bg-violet-500 animate-pulse',
    pillBg: 'bg-violet-100',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
  ended: {
    badgeText: 'CALL ENDED',
    hindiText: 'कॉल समाप्त',
    dotBg: 'bg-rose-500',
    pillBg: 'bg-rose-100',
    pillBorder: 'border-black',
    textColor: 'text-black',
  },
};

// Format duration
function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

// Microphone permission hook
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

  return { isDenied };
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
  const [activeNav, setActiveNav] = useState<'dashboard' | 'records' | 'history' | 'settings'>('dashboard');

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

  // Compute state
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
    <div className="flex h-screen w-full flex-col overflow-hidden bg-[#F4F4F0] font-sans text-black selection:bg-[#00F2FE] selection:text-black">
      {/* ── Top Header Navigation Bar ──────────────────────────────────────── */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b-2 border-black bg-white px-4 md:px-6">
        {/* Left Brand */}
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 items-center justify-center border-2 border-black bg-[#00F2FE] font-black text-black shadow-[2px_2px_0px_#000]">
              <Stethoscope className="size-4.5 stroke-[2.5]" />
            </div>
            <span className="text-lg font-black tracking-tight uppercase">
              HEALTHVOICE <span className="text-xs font-mono font-normal opacity-70">/ SAMAR</span>
            </span>
          </div>

          {/* Navigation Links */}
          <nav className="hidden items-center gap-2 md:flex">
            <button
              onClick={() => setActiveNav('dashboard')}
              className={cn(
                'border-2 border-black px-3.5 py-1 text-xs font-black tracking-wide uppercase transition-all',
                activeNav === 'dashboard'
                  ? 'bg-[#00F2FE] shadow-[2px_2px_0px_#000]'
                  : 'bg-white hover:bg-slate-100'
              )}
            >
              DASHBOARD
            </button>
            <button
              onClick={() => setActiveNav('records')}
              className={cn(
                'border-2 border-black px-3.5 py-1 text-xs font-black tracking-wide uppercase transition-all',
                activeNav === 'records'
                  ? 'bg-[#00F2FE] shadow-[2px_2px_0px_#000]'
                  : 'bg-white hover:bg-slate-100'
              )}
            >
              PATIENT RECORDS
            </button>
            <button
              onClick={() => setActiveNav('history')}
              className={cn(
                'border-2 border-black px-3.5 py-1 text-xs font-black tracking-wide uppercase transition-all',
                activeNav === 'history'
                  ? 'bg-[#00F2FE] shadow-[2px_2px_0px_#000]'
                  : 'bg-white hover:bg-slate-100'
              )}
            >
              SESSION HISTORY
            </button>
            <button
              onClick={() => setActiveNav('settings')}
              className={cn(
                'border-2 border-black px-3.5 py-1 text-xs font-black tracking-wide uppercase transition-all',
                activeNav === 'settings'
                  ? 'bg-[#00F2FE] shadow-[2px_2px_0px_#000]'
                  : 'bg-white hover:bg-slate-100'
              )}
            >
              SETTINGS
            </button>
          </nav>
        </div>

        {/* Right Status Tags */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 border-2 border-black bg-white px-3 py-1 text-[11px] font-mono font-bold tracking-tight shadow-[2px_2px_0px_#000]">
            <span className={cn('size-2 border border-black', isConnected ? 'bg-[#10B981]' : 'bg-slate-300')} />
            <span>
              {isConnected ? 'CONNECTED: E2E SECURE (12MS)' : 'DISCONNECTED: READY'}
            </span>
          </div>

          <button className="flex size-8 items-center justify-center border-2 border-black bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100">
            <Lock className="size-3.5 stroke-[2.5]" />
          </button>
          <button className="flex size-8 items-center justify-center border-2 border-black bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100">
            <BarChart2 className="size-3.5 stroke-[2.5]" />
          </button>
        </div>
      </header>

      {/* ── Microphone Error Warning Banner ────────────────────────────────── */}
      <AnimatePresence>
        {isDenied && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="border-b-2 border-black bg-[#FEF08A] px-4 py-2"
          >
            <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 text-xs font-bold">
              <div className="flex items-center gap-2">
                <AlertCircle className="size-4.5 text-black" />
                <span>MICROPHONE BLOCKED: Samar cannot hear your voice until microphone permissions are enabled.</span>
              </div>
              <button
                onClick={() => {
                  navigator.mediaDevices?.getUserMedia({ audio: true }).catch(() => {});
                }}
                className="border-2 border-black bg-white px-3 py-0.5 text-xs font-black shadow-[2px_2px_0px_#000] hover:bg-black hover:text-white"
              >
                RETRY ACCESS
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Main Layout Body ───────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">
        {/* ── Left Sidebar (Clinical Operator) ─────────────────────────────── */}
        <aside className="flex w-60 shrink-0 flex-col justify-between border-r-2 border-black bg-white p-4">
          <div>
            {/* Operator Card */}
            <div className="flex items-center gap-3 border-2 border-black bg-[#F8F9FA] p-3 shadow-[2px_2px_0px_#000]">
              <div className="flex size-10 shrink-0 items-center justify-center border-2 border-black bg-[#00F2FE] font-black text-black">
                <Stethoscope className="size-6 stroke-[2.5]" />
              </div>
              <div>
                <p className="text-sm font-black tracking-tight uppercase">DR. SAMAR</p>
                <p className="text-[10px] font-mono font-bold text-slate-600 uppercase">CLINICAL SPECIALIST</p>
              </div>
            </div>

            {/* New Session Button */}
            <button
              onClick={() => {
                if (!isConnected) start();
              }}
              className="mt-4 flex w-full items-center justify-center gap-2 border-2 border-black bg-[#00F2FE] py-2.5 text-xs font-black tracking-wider text-black uppercase shadow-[3px_3px_0px_#000] transition-all hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-[1px_1px_0px_#000] active:translate-x-1 active:translate-y-1 active:shadow-none"
            >
              <Phone className="size-3.5 stroke-[3]" />
              <span>NEW SESSION</span>
            </button>

            {/* Nav Menu */}
            <div className="mt-5 flex flex-col gap-2">
              <button
                onClick={() => setActiveNav('dashboard')}
                className={cn(
                  'flex items-center gap-3 border-2 border-black p-2.5 text-xs font-black uppercase transition-all',
                  activeNav === 'dashboard'
                    ? 'bg-[#00F2FE] shadow-[3px_3px_0px_#000]'
                    : 'bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100'
                )}
              >
                <Activity className="size-4 stroke-[2.5]" />
                <span>DASHBOARD</span>
              </button>

              <button
                onClick={() => setActiveNav('records')}
                className={cn(
                  'flex items-center gap-3 border-2 border-black p-2.5 text-xs font-black uppercase transition-all',
                  activeNav === 'records'
                    ? 'bg-[#00F2FE] shadow-[3px_3px_0px_#000]'
                    : 'bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100'
                )}
              >
                <Folder className="size-4 stroke-[2.5]" />
                <span>PATIENT RECORDS</span>
              </button>

              <button
                onClick={() => setActiveNav('history')}
                className={cn(
                  'flex items-center gap-3 border-2 border-black p-2.5 text-xs font-black uppercase transition-all',
                  activeNav === 'history'
                    ? 'bg-[#00F2FE] shadow-[3px_3px_0px_#000]'
                    : 'bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100'
                )}
              >
                <History className="size-4 stroke-[2.5]" />
                <span>SESSION HISTORY</span>
              </button>

              <button
                onClick={() => setActiveNav('settings')}
                className={cn(
                  'flex items-center gap-3 border-2 border-black p-2.5 text-xs font-black uppercase transition-all',
                  activeNav === 'settings'
                    ? 'bg-[#00F2FE] shadow-[3px_3px_0px_#000]'
                    : 'bg-white shadow-[2px_2px_0px_#000] hover:bg-slate-100'
                )}
              >
                <Settings className="size-4 stroke-[2.5]" />
                <span>SETTINGS</span>
              </button>
            </div>
          </div>

          {/* Pipeline Badge */}
          <div className="border-2 border-dashed border-black bg-[#FAF9F6] p-2.5 text-[10px] font-mono">
            <p className="font-bold text-black uppercase">VOICE STACK:</p>
            <p className="text-slate-700">• TTS: Murf Falcon (hi-IN)</p>
            <p className="text-slate-700">• STT: Deepgram Nova-3</p>
            <p className="text-slate-700">• LLM: Gemini 2.0</p>
          </div>
        </aside>

        {/* ── Main Workspace ─────────────────────────────────────────────────── */}
        <main className="flex flex-1 flex-col overflow-y-auto p-4 md:p-6">
          {/* Header of Main: Title + Recording tag + Timer */}
          <div className="flex flex-wrap items-center justify-between gap-4 border-b-2 border-black pb-4">
            <div>
              <h1 className="text-2xl font-black tracking-tight uppercase md:text-3xl">
                ACTIVE CONSULTATION
              </h1>
              <p className="font-mono text-xs font-bold text-slate-700 uppercase">
                PATIENT ID: #84932 • INTAKE SESSION • PHC RURAL TRIAGE
              </p>
            </div>

            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2 border-2 border-black bg-[#FF3366] px-3.5 py-1 text-xs font-black tracking-wider text-white uppercase shadow-[2px_2px_0px_#000]">
                <span className="size-2 rounded-full bg-white animate-ping" />
                <span>{isConnected ? 'RECORDING' : 'IDLE'}</span>
              </div>

              <div className="border-2 border-black bg-white px-3.5 py-1 font-mono text-sm font-black shadow-[2px_2px_0px_#000]">
                {formatDuration(sessionSeconds)}
              </div>
            </div>
          </div>

          {/* Consultation Grid (Center Visualizer + Right Context) */}
          <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-3">
            {/* Left 2 Cols: Visualizer & Live Transcript */}
            <div className="flex flex-col gap-5 lg:col-span-2">
              {/* ── Voice Visualizer Card ───────────────────────────────────── */}
              <div className="relative flex flex-col items-center justify-between border-2 border-black bg-[#F8F9FA] p-5 shadow-[4px_4px_0px_#000]">
                {/* Agent Listening Badge on Top Left */}
                <div className="flex w-full items-center justify-between">
                  <div className="flex items-center gap-2 border-2 border-black bg-white px-3 py-1 text-xs font-black uppercase shadow-[2px_2px_0px_#000]">
                    <Mic className="size-3.5 stroke-[2.5]" />
                    <span>{stateMeta.badgeText}</span>
                    <span className="font-normal opacity-60">|</span>
                    <span className="font-sans text-[11px] font-bold text-slate-700">
                      {stateMeta.hindiText}
                    </span>
                  </div>

                  <span className="font-mono text-[11px] font-bold uppercase text-slate-600">
                    CHANNEL: STEREO 48KHZ
                  </span>
                </div>

                {/* LiveKit Wave Sine Shader Visualizer */}
                <div className="relative my-4 flex h-[220px] w-full max-w-[480px] items-center justify-center sm:h-[250px] md:h-[280px]">
                  <AgentAudioVisualizerWave
                    size="xl"
                    state={
                      dashState === 'ready'
                        ? 'disconnected'
                        : dashState === 'ended'
                          ? 'disconnected'
                          : (agentState ?? 'listening')
                    }
                    color={
                      dashState === 'speaking'
                        ? '#6D28D9' // deep rich violet
                        : dashState === 'listening'
                          ? '#047857' // deep emerald
                          : dashState === 'thinking'
                            ? '#0369A1' // deep sapphire blue
                            : dashState === 'connecting'
                              ? '#D97706' // deep amber
                              : '#0891B2' // deep dark cyan
                    }
                    colorShift={0.3}
                    lineWidth={3}
                    blur={0.2}
                    audioTrack={audioTrack}
                    className="size-full"
                  />

                  {/* Ready CTA Overlay when disconnected */}
                  {!isConnected && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center bg-[#F8F9FA]/75 backdrop-blur-[1.5px]">
                      <button
                        onClick={() => start()}
                        className="group flex items-center gap-2.5 border-2 border-black bg-[#00F2FE] px-6 py-3 text-xs font-black uppercase text-black shadow-[4px_4px_0px_#000] transition-all hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-[2px_2px_0px_#000] active:translate-x-1 active:translate-y-1 active:shadow-none"
                      >
                        {hasStartedOnce ? (
                          <>
                            <RotateCcw className="size-4 stroke-[3] transition-transform group-hover:-rotate-90" />
                            <span>RECONNECT SESSION (पुनः जुड़ें)</span>
                          </>
                        ) : (
                          <>
                            <Phone className="size-4 stroke-[3] transition-transform group-hover:scale-110" />
                            <span>START CONSULTATION (कॉल शुरू करें)</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>

                {/* Bottom Voice Controls */}
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => {
                      if (isConnected) microphoneToggle.toggle();
                    }}
                    disabled={!isConnected}
                    className={cn(
                      'flex items-center gap-2 border-2 border-black px-4 py-1.5 text-xs font-black uppercase shadow-[2px_2px_0px_#000] transition-all disabled:opacity-50',
                      microphoneToggle.enabled
                        ? 'bg-white hover:bg-slate-100'
                        : 'bg-[#FF3366] text-white hover:bg-[#EF4444]'
                    )}
                  >
                    {microphoneToggle.enabled ? (
                      <>
                        <Mic className="size-4 stroke-[2.5]" />
                        <span>MUTE MIC</span>
                      </>
                    ) : (
                      <>
                        <MicOff className="size-4 stroke-[2.5]" />
                        <span>UNMUTE MIC</span>
                      </>
                    )}
                  </button>

                  <button
                    onClick={() => {
                      if (isConnected) end();
                    }}
                    disabled={!isConnected}
                    className="flex items-center gap-2 border-2 border-black bg-[#FF3366] px-4 py-1.5 text-xs font-black text-white uppercase shadow-[2px_2px_0px_#000] transition-all hover:bg-[#EF4444] disabled:opacity-50"
                  >
                    <PhoneOff className="size-4 stroke-[2.5]" />
                    <span>END CALL</span>
                  </button>
                </div>
              </div>

              {/* ── Live Transcript Card ────────────────────────────────────── */}
              <div className="border-2 border-black bg-white p-4 shadow-[4px_4px_0px_#000]">
                <div className="flex items-center justify-between border-b-2 border-black pb-2">
                  <div className="flex items-center gap-2">
                    <FileText className="size-4 stroke-[2.5]" />
                    <span className="font-mono text-xs font-black tracking-wider uppercase">
                      LIVE TRANSCRIPT
                    </span>
                  </div>
                  <span className="font-mono text-[10px] font-bold uppercase text-slate-500">
                    DEVANAGARI & EN SPEECH
                  </span>
                </div>

                {/* Messages Container */}
                <div className="mt-3 flex max-h-56 flex-col gap-3 overflow-y-auto pr-1">
                  {messages.length === 0 ? (
                    <div className="flex flex-col gap-2 py-4 text-xs font-medium text-slate-700">
                      <div className="flex items-start gap-2">
                        <span className="border-2 border-black bg-[#00F2FE] px-2 py-0.5 font-mono text-[10px] font-black text-black shadow-[1px_1px_0px_#000]">
                          DR. ARIS / SAMAR:
                        </span>
                        <p className="font-medium text-black">
                          "नमस्ते! मैं प्राथमिक स्वास्थ्य केंद्र से समर बोल रहा हूँ। आप अपनी समस्या बताइए।"
                        </p>
                      </div>

                      <div className="flex items-start gap-2">
                        <span className="font-mono text-[10px] font-black text-slate-700 uppercase">
                          PATIENT (CALLER):
                        </span>
                        <p className="text-slate-800">
                          "Meri beti ko do din se tez bukhar aur sar dard hai..."
                        </p>
                      </div>
                    </div>
                  ) : (
                    messages.map((m) => {
                      const isUser = m.from?.isLocal;
                      return (
                        <div key={m.id} className="flex items-start gap-2 text-xs">
                          {isUser ? (
                            <span className="shrink-0 font-mono text-[10px] font-black uppercase text-slate-700">
                              PATIENT:
                            </span>
                          ) : (
                            <span className="shrink-0 border-2 border-black bg-[#00F2FE] px-1.5 py-0.2 font-mono text-[10px] font-black text-black shadow-[1px_1px_0px_#000]">
                              SAMAR:
                            </span>
                          )}
                          <p className="font-medium text-black">{m.message}</p>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>

            {/* Right Col: Patient Context & Extracted Entities ─────────────────── */}
            <div className="flex flex-col gap-5">
              {/* ── Patient Context Card ─────────────────────────────────────── */}
              <div className="border-2 border-black bg-white shadow-[4px_4px_0px_#000]">
                <div className="border-b-2 border-black bg-black px-3 py-1.5 text-xs font-black tracking-widest text-white uppercase">
                  PATIENT CONTEXT
                </div>
                <div className="p-3.5">
                  <div className="flex justify-between border-b border-black/20 pb-2 font-mono text-xs font-bold">
                    <span className="text-slate-600 uppercase">VITALS LAST CHECK</span>
                    <span className="text-black">OCT 24, 2026</span>
                  </div>

                  <div className="flex justify-between border-b border-black/20 py-2 font-mono text-xs font-bold">
                    <span className="text-slate-600 uppercase">BP</span>
                    <span className="text-black">120/80</span>
                  </div>

                  <div className="flex items-center justify-between py-2 font-mono text-xs font-bold">
                    <span className="text-slate-600 uppercase">SCHEME / ALLERGIES</span>
                    <span className="border border-black bg-[#FF3366] px-2 py-0.5 font-mono text-[10px] font-black text-white uppercase shadow-[1px_1px_0px_#000]">
                      PMJAY / AYUSHMAN
                    </span>
                  </div>

                  <button className="mt-3 w-full border-2 border-black bg-white py-1.5 text-center text-xs font-black tracking-wider uppercase shadow-[2px_2px_0px_#000] hover:bg-slate-100">
                    VIEW FULL PROFILE
                  </button>
                </div>
              </div>

              {/* ── Extracted Entities Card ──────────────────────────────────── */}
              <div className="border-2 border-black bg-white shadow-[4px_4px_0px_#000]">
                <div className="border-b-2 border-black bg-black px-3 py-1.5 text-xs font-black tracking-widest text-white uppercase">
                  EXTRACTED ENTITIES
                </div>
                <div className="flex flex-col gap-2.5 p-3.5">
                  <div className="flex items-center justify-between border-2 border-black bg-[#F0FDF4] p-2 font-mono text-[11px] font-bold shadow-[1px_1px_0px_#000]">
                    <span>SYMPTOM: HIGH FEVER</span>
                    <CheckCircle2 className="size-3.5 text-[#10B981]" />
                  </div>

                  <div className="flex items-center justify-between border-2 border-black bg-[#F0FDF4] p-2 font-mono text-[11px] font-bold shadow-[1px_1px_0px_#000]">
                    <span>ESCALATION: PHC VISIT TODAY</span>
                    <CheckCircle2 className="size-3.5 text-[#10B981]" />
                  </div>

                  <div className="flex items-center justify-between border-2 border-dashed border-black bg-[#FAF9F6] p-2 font-mono text-[11px] font-bold text-slate-600">
                    <span className="italic">ANALYZING SEVERITY...</span>
                    <RotateCcw className="size-3.5 animate-spin" />
                  </div>

                  <button className="mt-1 w-full border-2 border-black bg-[#00F2FE] py-2 text-center text-xs font-black tracking-wider uppercase shadow-[3px_3px_0px_#000] transition-all hover:translate-x-0.5 hover:translate-y-0.5 hover:shadow-[1px_1px_0px_#000]">
                    GENERATE DRAFT NOTE
                  </button>
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
