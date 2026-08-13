'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTheme } from 'next-themes';
import Image from 'next/image';
import { RoomEvent } from 'livekit-client';
import {
  Activity,
  AlertCircle,
  Building2,
  ChevronDown,
  Clock,
  HeartPulse,
  Loader2,
  Map as MapIcon,
  MapPin,
  MessageSquare,
  Mic,
  MicOff,
  Moon,
  Navigation,
  PanelLeftClose,
  PanelLeftOpen,
  Phone,
  PhoneOff,
  Plus,
  Search,
  SendHorizontal,
  Settings,
  Signal,
  Sparkles,
  Sun,
  Trash2,
  X,
} from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { toast } from 'sonner';
import {
  useAgent,
  useChat,
  useRoomContext,
  useSessionContext,
  useSessionMessages,
  useVoiceAssistant,
} from '@livekit/components-react';
import { AgentAudioVisualizerWave } from '@/components/agents-ui/agent-audio-visualizer-wave';
import { CallAnalyticsDashboard } from '@/components/app/call-analytics-dashboard';
import { useInputControls } from '@/hooks/agents-ui/use-agent-control-bar';
import { cn } from '@/lib/shadcn/utils';

export type DashState = 'ready' | 'connecting' | 'listening' | 'speaking' | 'ended';

export interface FacilityInfo {
  id: string;
  name: string;
  facility_type: string;
  district: string;
  state: string;
  pincode: string;
  address: string;
  lat?: number;
  lon?: number;
  opd_timings: string;
  emergency_24x7: boolean;
  contact_number: string;
  ambulance_available: boolean;
  available_doctors: string[];
  free_services: string[];
  verified_timestamp?: string;
}

interface StateMeta {
  badgeText: string;
  statusHint: string;
  dotBg: string;
  pillBg: string;
  textColor: string;
}

const STATE_CONFIG: Record<DashState, StateMeta> = {
  ready: {
    badgeText: 'READY',
    statusHint: 'The agent has not started yet',
    dotBg: 'bg-slate-400 dark:bg-slate-300',
    pillBg: 'bg-white/90 dark:bg-slate-800/90',
    textColor: 'text-slate-700 dark:text-slate-200',
  },
  connecting: {
    badgeText: 'CONNECTING',
    statusHint: 'The agent is joining the call; please wait',
    dotBg: 'bg-amber-400 animate-ping',
    pillBg: 'bg-amber-100/90 dark:bg-amber-950/70',
    textColor: 'text-amber-900 dark:text-amber-300',
  },
  listening: {
    badgeText: 'LISTENING TO YOU',
    statusHint: 'Listening to you',
    dotBg: 'bg-emerald-400 animate-pulse',
    pillBg: 'bg-emerald-100/90 dark:bg-emerald-950/70',
    textColor: 'text-emerald-900 dark:text-emerald-300',
  },
  speaking: {
    badgeText: 'AGENT IS SPEAKING',
    statusHint: 'Agent is speaking',
    dotBg: 'bg-violet-500 animate-pulse',
    pillBg: 'bg-violet-100/90 dark:bg-violet-950/70',
    textColor: 'text-violet-900 dark:text-violet-300',
  },
  ended: {
    badgeText: 'CALL ENDED',
    statusHint: 'The conversation is over',
    dotBg: 'bg-rose-500',
    pillBg: 'bg-rose-100/90 dark:bg-rose-950/70',
    textColor: 'text-rose-900 dark:text-rose-300',
  },
};

export interface ChatMessageItem {
  id: string;
  message: string;
  isUser: boolean;
  timestamp: number;
}

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessageItem[];
}

const STORAGE_KEY = 'careva_chat_sessions_v1';

const _rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: 'narrow' });
function formatRelativeTime(timestamp: number): string {
  const diffSec = (timestamp - Date.now()) / 1000;
  if (Math.abs(diffSec) < 60) return 'just now';
  if (Math.abs(diffSec) < 3600) return _rtf.format(Math.round(diffSec / 60), 'minute');
  if (Math.abs(diffSec) < 86400) return _rtf.format(Math.round(diffSec / 3600), 'hour');
  return _rtf.format(Math.round(diffSec / 86400), 'day');
}

// Add entries here to support more languages; key must match selectedLang value
const LANGUAGES = [
  { value: 'hi', label: '🇮🇳 हिन्दी', short: '🇮🇳 HI' },
  { value: 'en', label: '🇬🇧 English', short: '🇬🇧 EN' },
] satisfies { value: string; label: string; short: string }[];

const QUICK_SUGGESTIONS: Record<string, string[]> = {
  hi: [
    '2 दिन से तेज बुखार है',
    'डॉक्टर से परामर्श चाहिए',
    'पास का PHC केंद्र कहाँ है?',
    'PMJAY योजना में क्या लाभ हैं?',
  ],
  en: [
    'Fever & cough for 2 days',
    'Need doctor consultation',
    'Nearest health center?',
    'PMJAY scheme benefits?',
  ],
};

function toChatItems(
  rawMessages: Array<{
    id: string;
    message: string;
    from?: { isLocal?: boolean };
    createdAt?: string | number | Date;
  }>
): ChatMessageItem[] {
  return rawMessages.map((m) => ({
    id: m.id,
    message: m.message,
    isUser: !!m.from?.isLocal,
    timestamp: m.createdAt ? new Date(m.createdAt).getTime() : Date.now(),
  }));
}

function mergeMessages(existing: ChatMessageItem[], live: ChatMessageItem[]): ChatMessageItem[] {
  const indexMap = new Map(existing.map((m, idx) => [m.id, idx]));
  const merged = [...existing];

  for (const item of live) {
    const existingIdx = indexMap.get(item.id);
    if (existingIdx !== undefined) {
      merged[existingIdx] = {
        ...merged[existingIdx],
        message: item.message,
        timestamp: item.timestamp || merged[existingIdx].timestamp,
      };
    } else {
      indexMap.set(item.id, merged.length);
      merged.push(item);
    }
  }
  return merged;
}

interface NavItemProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  active?: boolean;
  isSidebarOpen: boolean;
  onClick: () => void;
  title?: string;
  badge?: boolean;
  rightElement?: React.ReactNode;
  variant?: 'default' | 'primary';
  className?: string;
}

function NavItem({
  icon: Icon,
  label,
  active = false,
  isSidebarOpen,
  onClick,
  title,
  badge = false,
  rightElement,
  variant = 'default',
  className,
}: NavItemProps) {
  const isPrimary = variant === 'primary';
  const activeClass =
    isPrimary || active
      ? 'clay-btn-primary text-slate-950'
      : 'clay-btn text-slate-700 hover:text-slate-950';

  if (isSidebarOpen) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'flex w-full items-center justify-between p-2.5 text-xs font-black uppercase transition-all',
          activeClass,
          isPrimary &&
            'justify-center gap-2 py-2.5 tracking-wider hover:scale-[1.02] active:scale-95',
          className
        )}
        title={title || label}
      >
        <div className="flex items-center gap-2.5">
          <Icon className="size-4 stroke-[2.5]" />
          <span>{label}</span>
        </div>
        {rightElement}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'clay-btn relative flex size-11 items-center justify-center rounded-2xl transition-all hover:scale-105 active:scale-95',
        activeClass,
        className
      )}
      title={title || label}
    >
      <Icon className="size-5 stroke-[2.5]" />
      {badge && (
        <span className="absolute top-1.5 right-1.5 size-2 rounded-full bg-sky-500 ring-2 ring-white" />
      )}
    </button>
  );
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
  const { send } = useChat();
  const { isDenied } = useMicStatus();
  const { microphoneToggle } = useInputControls();
  const room = useRoomContext();

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [hasStartedOnce, setHasStartedOnce] = useState(false);
  const [selectedLang, setSelectedLang] = useState<string>('hi');
  const [isLangOpen, setIsLangOpen] = useState(false);
  const [livePing, setLivePing] = useState<number | null>(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isChatsDropdownOpen, setIsChatsDropdownOpen] = useState(true);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeNav, setActiveNav] = useState<'new_chat' | 'chats' | 'dashboard' | 'settings'>(
    'dashboard'
  );
  // Navbar Theme state
  const { theme, setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [isForgetting, setIsForgetting] = useState(false);
  const [activeFacility, setActiveFacility] = useState<FacilityInfo | null>(null);
  const [facilityTimestamp, setFacilityTimestamp] = useState<string>('Live Public API');
  const [showFacilityMap, setShowFacilityMap] = useState(true);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!room) return;
    const onDataReceived = (
      payload: Uint8Array,
      _participant?: unknown,
      _kind?: unknown,
      topic?: string
    ) => {
      if (topic === 'facility_card') {
        try {
          const text = new TextDecoder().decode(payload);
          const data = JSON.parse(text);
          if (data?.facility) {
            setActiveFacility(data.facility);
            if (data.timestamp) setFacilityTimestamp(data.timestamp);
            toast.success(`Nearest facility found: ${data.facility.name}`);
          }
        } catch (err) {
          console.error('Error parsing facility_card:', err);
        }
      } else if (topic === 'escalation_card') {
        // Day 7 — the agent handed this caller to a human; show the reference number.
        try {
          const data = JSON.parse(new TextDecoder().decode(payload));
          if (data?.ref) {
            toast.warning(`Sent to a human health worker — ${data.ref}`, {
              description: `Urgency: ${data.urgency}. A health worker will review it during working hours.`,
              duration: 12000,
            });
          }
        } catch (err) {
          console.error('Error parsing escalation_card:', err);
        }
      }
    };

    room.on(RoomEvent.DataReceived, onDataReceived);
    return () => {
      room.off(RoomEvent.DataReceived, onDataReceived);
    };
  }, [room]);

  const handleForgetMe = async () => {
    try {
      setIsForgetting(true);
      const res = await fetch('/api/forget', { method: 'POST' });
      if (res.ok) {
        toast.success('Personal consultation memory erased from database.');
      } else {
        toast.error('Failed to erase memory.');
      }
    } catch (err) {
      console.error('Forget me error:', err);
      toast.error('Error erasing records.');
    } finally {
      setIsForgetting(false);
    }
  };

  const isDark = mounted && (theme === 'dark' || resolvedTheme === 'dark');

  // WebRTC RTT latency — uses standard RTCPeerConnection.getStats() (browser API).
  // Engine access is cast to `any` with a silent catch so LiveKit internal refactors
  // don't break the app; ping display is cosmetic and degrades gracefully.
  // ponytail: no named public getter in livekit-client 2.x; swap to room.engine.publisherRTCPeerConnection if that lands
  useEffect(() => {
    if (!isConnected) {
      setLivePing(null);
      return;
    }
    let cancelled = false;

    const measure = async () => {
      try {
        const eng = (
          room as unknown as {
            engine?: {
              publisherRTCPeerConnection?: RTCPeerConnection;
              publisher?: { pc?: RTCPeerConnection };
            };
          }
        )?.engine;
        const pc: RTCPeerConnection | undefined =
          eng?.publisherRTCPeerConnection ?? eng?.publisher?.pc;
        if (!pc || pc.connectionState !== 'connected') return;
        for (const report of (await pc.getStats()).values()) {
          if (
            report.type === 'candidate-pair' &&
            report.nominated &&
            typeof report.currentRoundTripTime === 'number'
          ) {
            if (!cancelled) setLivePing(Math.round(report.currentRoundTripTime * 1000));
            return;
          }
        }
      } catch {
        // silently ignore — ping is cosmetic
      }
    };

    measure();
    const id = setInterval(measure, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [isConnected, room]);

  // Chat input state
  const [chatInputText, setChatInputText] = useState('');
  const [isSendingChat, setIsSendingChat] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Load saved sessions from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        const parsed: ChatSession[] = JSON.parse(stored);
        if (Array.isArray(parsed)) {
          setSessions(parsed);
        }
      }
    } catch (e) {
      console.error('Failed to load chat history:', e);
    }
  }, []);

  // Active session object
  const activeSession = useMemo(() => {
    return sessions.find((s) => s.id === activeChatId) || null;
  }, [sessions, activeChatId]);

  // Filtered sessions based on search query
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const q = searchQuery.toLowerCase();
    return sessions.filter(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.messages.some((m) => m.message.toLowerCase().includes(q))
    );
  }, [sessions, searchQuery]);

  // Combined messages to display in the chat stream (updating real-time speech tokens)
  const displayedMessages = useMemo<ChatMessageItem[]>(() => {
    const liveItems = toChatItems(messages);
    if (!activeSession) return liveItems;
    return mergeMessages(activeSession.messages, liveItems);
  }, [messages, activeSession]);

  // Auto-scroll to bottom as messages or streaming words arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [displayedMessages]);

  // Sync live LiveKit messages into persistent active chat session
  useEffect(() => {
    if (messages.length === 0) return;
    const currentLiveMsgs = toChatItems(messages);

    setSessions((prevSessions) => {
      let currentId = activeChatId;
      if (!currentId) {
        currentId = `chat_${Date.now()}`;
        setActiveChatId(currentId);
      }

      const existingIndex = prevSessions.findIndex((s) => s.id === currentId);
      let updated: ChatSession[];

      if (existingIndex >= 0) {
        const existing = prevSessions[existingIndex];
        const mergedMessages = mergeMessages(existing.messages, currentLiveMsgs);

        const firstUserMsg = mergedMessages.find((m) => m.isUser)?.message;
        const autoTitle = firstUserMsg
          ? firstUserMsg.length > 26
            ? firstUserMsg.slice(0, 26) + '...'
            : firstUserMsg
          : mergedMessages[0]?.message.slice(0, 26) || existing.title;

        const updatedSession: ChatSession = {
          ...existing,
          title:
            existing.title === 'New Consultation' || existing.title === 'Consultation'
              ? autoTitle
              : existing.title,
          updatedAt: Date.now(),
          messages: mergedMessages,
        };
        updated = [updatedSession, ...prevSessions.filter((_, i) => i !== existingIndex)];
      } else {
        const firstUserMsg = currentLiveMsgs.find((m) => m.isUser)?.message;
        const autoTitle = firstUserMsg
          ? firstUserMsg.length > 26
            ? firstUserMsg.slice(0, 26) + '...'
            : firstUserMsg
          : currentLiveMsgs[0]?.message.slice(0, 26) || 'Consultation';

        const newSession: ChatSession = {
          id: currentId,
          title: autoTitle,
          createdAt: Date.now(),
          updatedAt: Date.now(),
          messages: currentLiveMsgs,
        };
        updated = [newSession, ...prevSessions];
      }

      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch (err) {
        console.error('Failed to save chat sessions:', err);
      }
      return updated;
    });
  }, [messages, activeChatId]);

  // ChatGPT-like New Chat handler: resets to fresh Ready state without auto-starting call
  const handleNewChat = () => {
    if (isConnected) {
      end();
    }
    setActiveChatId(null);
    setHasStartedOnce(false);
    setChatInputText('');
    setActiveNav('new_chat');
  };

  // Select a previous chat session to view/continue
  const handleSelectChat = (chat: ChatSession) => {
    if (isConnected) {
      end();
    }
    setActiveChatId(chat.id);
    setActiveNav('chats');
    setHasStartedOnce(true);
  };

  // Delete a chat session
  const handleDeleteChat = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    setSessions((prev) => {
      const updated = prev.filter((s) => s.id !== chatId);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
      } catch {}
      return updated;
    });
    if (activeChatId === chatId) {
      handleNewChat();
    }
  };

  // Track session timer
  useEffect(() => {
    if (isConnected) {
      setHasStartedOnce(true);
    }
  }, [isConnected]);

  // Handle sending chat message via LiveKit
  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend ?? chatInputText).trim();
    if (!text || isSendingChat) return;

    try {
      setIsSendingChat(true);
      // Auto-start session if not connected
      if (!isConnected) {
        await start();
      }
      await send(text);
      setChatInputText('');
    } catch (error) {
      console.error('Error sending message:', error);
    } finally {
      setIsSendingChat(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Compute state
  const dashState = useMemo<DashState>(() => {
    if (!isConnected) {
      return hasStartedOnce || displayedMessages.length > 0 ? 'ended' : 'ready';
    }
    const stateString = (agentState ?? vaState ?? '').toString();
    switch (stateString) {
      case 'connecting':
      case 'initializing':
        return 'connecting';
      case 'speaking':
        return 'speaking';
      case 'listening':
      case 'thinking':
      default:
        return 'listening';
    }
  }, [isConnected, hasStartedOnce, displayedMessages.length, agentState, vaState]);

  const stateMeta = STATE_CONFIG[dashState];

  return (
    <div className="flex h-screen w-full gap-3 overflow-hidden bg-gradient-to-br from-[#E8EEF5] via-[#EDF2F7] to-[#DFE7F0] p-3 font-sans text-slate-800 transition-colors duration-200 selection:bg-[#00F2FE] selection:text-slate-950 md:p-4 dark:from-[#070c14] dark:via-[#090f18] dark:to-[#05080e] dark:text-slate-100">
      {/* ── Left Collapsible Clay Sidebar (Full Height, Connected) ─────────── */}
      <motion.aside
        animate={{ width: isSidebarOpen ? 260 : 72 }}
        transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
        className={cn(
          'clay-card flex shrink-0 flex-col justify-between overflow-hidden transition-[padding] duration-200',
          isSidebarOpen ? 'w-[260px] p-3.5' : 'w-[72px] items-center p-2.5'
        )}
      >
        {/* Top Section */}
        <div className="flex w-full flex-col">
          {/* Operator Card / Collapsed Icon (Matching Screenshot Top Bar) */}
          {isSidebarOpen ? (
            <div className="clay-card-flat flex items-center justify-between gap-2 p-1.5">
              {/* Left: Circular Careva Logo + CAREVA */}
              <div className="flex items-center gap-2.5 overflow-hidden">
                <div className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-full shadow-sm ring-1 ring-slate-200/80 dark:ring-white/10">
                  <Image
                    src="/careva.png"
                    alt="Careva Logo"
                    width={36}
                    height={36}
                    className="size-full object-cover"
                  />
                </div>
                <span className="truncate text-xs font-black tracking-tight text-slate-900 uppercase dark:text-slate-100">
                  CAREVA
                </span>
              </div>

              {/* Right: Circular Search & Circular Sidebar Collapse Buttons */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setIsSearchOpen((prev) => !prev)}
                  className={cn(
                    'clay-btn flex size-8 shrink-0 items-center justify-center rounded-full transition-all hover:scale-105 active:scale-95',
                    isSearchOpen
                      ? 'bg-sky-200 text-sky-950 dark:bg-cyan-900/80 dark:text-cyan-100'
                      : 'bg-white text-slate-700 hover:text-slate-950 dark:bg-[#152030] dark:text-slate-200 dark:hover:text-white'
                  )}
                  title="Search chats"
                  aria-label="Search chats"
                >
                  <Search className="size-3.5 stroke-[2.5]" />
                </button>
                <button
                  onClick={() => {
                    setIsSidebarOpen(false);
                    setIsSearchOpen(false);
                  }}
                  className="clay-btn flex size-8 shrink-0 items-center justify-center rounded-full bg-white text-slate-700 transition-all hover:scale-105 hover:text-slate-950 active:scale-95 dark:bg-[#152030] dark:text-slate-200 dark:hover:text-white"
                  title="Collapse sidebar"
                  aria-label="Collapse sidebar"
                >
                  <PanelLeftClose className="size-3.5 stroke-[2.5]" />
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center">
              <button
                onClick={() => setIsSidebarOpen(true)}
                className="group relative mx-auto flex size-11 shrink-0 items-center justify-center overflow-hidden rounded-full shadow-sm ring-2 ring-sky-300/40 transition-all hover:scale-105 active:scale-95 dark:ring-cyan-500/30"
                title="Open sidebar (Careva)"
                aria-label="Open sidebar"
              >
                {/* Default: Careva Logo */}
                <Image
                  src="/careva.png"
                  alt="Careva Logo"
                  width={44}
                  height={44}
                  className="size-full object-cover transition-all duration-200 group-hover:scale-75 group-hover:opacity-0"
                />
                {/* Hover: Sidebar Expand Icon */}
                <PanelLeftOpen className="absolute size-5 stroke-[2.5] text-slate-900 opacity-0 transition-all duration-200 group-hover:scale-100 group-hover:opacity-100 dark:text-cyan-400" />
              </button>
            </div>
          )}

          {/* Quick Search Input (when Search is clicked) */}
          <AnimatePresence>
            {isSidebarOpen && isSearchOpen && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18 }}
                className="mt-2"
              >
                <div className="clay-card-flat flex items-center gap-1.5 px-2.5 py-1.5">
                  <Search className="size-3.5 shrink-0 text-slate-400" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search chats..."
                    className="w-full bg-transparent text-[11px] font-medium text-slate-800 placeholder:text-slate-400 focus:outline-none"
                    autoFocus
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="text-slate-400 hover:text-slate-700"
                    >
                      <X className="size-3" />
                    </button>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* 1. New Chat Button */}
          <NavItem
            icon={Plus}
            label="NEW CHAT"
            variant="primary"
            isSidebarOpen={isSidebarOpen}
            onClick={handleNewChat}
            title={isSidebarOpen ? 'Start a new chat session' : 'New Chat'}
            className="mt-3"
          />

          {/* Nav Menu Items */}
          <div className={cn('mt-3 flex flex-col gap-1.5', !isSidebarOpen && 'items-center')}>
            {/* 2. Chats (with collapsible dropdown for history) */}
            <div className={cn('flex flex-col gap-1', isSidebarOpen && 'w-full')}>
              <NavItem
                icon={MessageSquare}
                label="CHATS"
                active={activeNav === 'chats'}
                isSidebarOpen={isSidebarOpen}
                onClick={() => {
                  if (!isSidebarOpen) {
                    setIsSidebarOpen(true);
                    setIsChatsDropdownOpen(true);
                  } else {
                    setIsChatsDropdownOpen((prev) => !prev);
                  }
                  setActiveNav('chats');
                }}
                title={
                  isSidebarOpen ? 'Chats & Session History' : 'Chats (Click to expand history)'
                }
                badge={!isSidebarOpen}
                rightElement={
                  <ChevronDown
                    className={cn(
                      'size-3.5 stroke-[2.5] text-slate-600 transition-transform duration-200',
                      isChatsDropdownOpen && 'rotate-180'
                    )}
                  />
                }
              />

              {/* Dropdown Chat History List */}
              <AnimatePresence>
                {isSidebarOpen && isChatsDropdownOpen && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.18 }}
                    className="clay-inset flex max-h-56 flex-col gap-1 overflow-y-auto rounded-2xl p-1.5"
                  >
                    {filteredSessions.length === 0 ? (
                      <div className="py-3 text-center font-mono text-[10px] font-bold text-slate-400">
                        {searchQuery ? 'NO MATCHING CHATS' : 'NO RECENT CHATS YET'}
                      </div>
                    ) : (
                      filteredSessions.map((chat) => {
                        const isActive = chat.id === activeChatId;
                        return (
                          <div
                            key={chat.id}
                            onClick={() => handleSelectChat(chat)}
                            className={cn(
                              'group flex w-full cursor-pointer items-center justify-between rounded-xl p-2 text-left transition-all active:scale-[0.98]',
                              isActive
                                ? 'bg-sky-100/90 ring-1 ring-sky-300 dark:bg-cyan-950/60 dark:ring-cyan-600/60'
                                : 'hover:bg-white/80 dark:hover:bg-slate-800/60'
                            )}
                          >
                            <div className="min-w-0 flex-1 overflow-hidden pr-1">
                              <div className="flex w-full items-center justify-between gap-1">
                                <span
                                  className={cn(
                                    'truncate text-[11px] font-black',
                                    isActive
                                      ? 'text-sky-950 dark:text-cyan-200'
                                      : 'text-slate-800 dark:text-slate-200'
                                  )}
                                >
                                  {chat.title}
                                </span>
                              </div>
                              <div className="mt-0.5 flex items-center justify-between gap-1 font-mono text-[9px] text-slate-400 dark:text-slate-500">
                                <span>{formatRelativeTime(chat.updatedAt)}</span>
                                <span>{chat.messages.length} msgs</span>
                              </div>
                            </div>

                            <button
                              type="button"
                              onClick={(e) => handleDeleteChat(e, chat.id)}
                              className="p-1 text-slate-400 opacity-0 transition-opacity group-hover:opacity-100 hover:text-rose-600"
                              title="Delete chat"
                            >
                              <Trash2 className="size-3" />
                            </button>
                          </div>
                        );
                      })
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* 3. Dashboard */}
            <NavItem
              icon={Activity}
              label="DASHBOARD"
              active={activeNav === 'dashboard'}
              isSidebarOpen={isSidebarOpen}
              onClick={() => setActiveNav('dashboard')}
              title="Dashboard"
            />

            {/* 4. Settings */}
            <NavItem
              icon={Settings}
              label="SETTINGS"
              active={activeNav === 'settings'}
              isSidebarOpen={isSidebarOpen}
              onClick={() => setActiveNav('settings')}
              title="Settings"
            />
          </div>
        </div>
      </motion.aside>

      {/* ── Right Main Area (Connected Top Header + Workspace) ─────────────── */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* ── Top Header (Shadcn Router Breadcrumbs on Left, Language + Theme + GitHub on Right) ── */}
        <header className="clay-card mb-3 flex h-14 shrink-0 items-center justify-between px-4 md:px-5">
          {/* Left: Shadcn-style Router Breadcrumbs */}
          <div className="flex items-center gap-2">
            {/* Breadcrumb Path */}
            <nav
              aria-label="Breadcrumb"
              className="flex items-center gap-1.5 text-xs font-semibold text-slate-500"
            >
              <button
                onClick={() => setActiveNav('dashboard')}
                className="group flex items-center gap-2 transition-all hover:opacity-85"
                title="Careva Dashboard"
              >
                <Image
                  src="/careva.png"
                  alt="Careva Logo"
                  width={24}
                  height={24}
                  className="size-6 rounded-full object-cover shadow-xs"
                />
                <span className="text-[13px] font-black tracking-tight text-slate-900 dark:text-white">
                  Careva<span className="text-sky-500 dark:text-cyan-400">.</span>
                </span>
              </button>

              <span className="font-mono text-slate-300 dark:text-slate-600">/</span>

              {activeNav === 'dashboard' && (
                <span className="font-bold text-slate-900 dark:text-slate-100">Dashboard</span>
              )}

              {activeNav === 'new_chat' && (
                <span className="font-bold text-slate-900 dark:text-slate-100">
                  New Consultation
                </span>
              )}

              {activeNav === 'chats' && (
                <>
                  <button
                    onClick={() => {
                      setIsChatsDropdownOpen(true);
                      if (!isSidebarOpen) setIsSidebarOpen(true);
                    }}
                    className="transition-colors hover:text-slate-900 dark:hover:text-white"
                  >
                    Chats
                  </button>
                  {activeSession && (
                    <>
                      <span className="font-mono text-slate-300">/</span>
                      <span className="max-w-[130px] truncate font-bold text-slate-900 sm:max-w-[200px] md:max-w-[280px] dark:text-slate-100">
                        {activeSession.title}
                      </span>
                    </>
                  )}
                </>
              )}

              {activeNav === 'settings' && (
                <span className="font-bold text-slate-900 dark:text-slate-100">Settings</span>
              )}
            </nav>
          </div>

          {/* Right: Forget Me link, Language Toggle, Theme Toggle, View GitHub */}
          <div className="flex items-center gap-2.5">
            {/* Underlined Forget Me Button */}
            <button
              onClick={handleForgetMe}
              disabled={isForgetting}
              className="flex cursor-pointer items-center gap-1.5 px-2 py-1 text-xs font-bold text-rose-600 underline decoration-rose-400 underline-offset-4 transition-all hover:text-rose-700 hover:decoration-rose-600 active:scale-95 disabled:opacity-50 dark:text-rose-400 dark:decoration-rose-500 dark:hover:text-rose-300"
              title="Permanently wipe your stored health consultation data"
              aria-label="Forget My Memory"
            >
              {isForgetting ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Trash2 className="size-3.5 stroke-[2.2]" />
              )}
              <span>Forget Me</span>
            </button>

            {/* Language selector — add entries to LANGUAGES to support more */}
            <div className="relative">
              <button
                onClick={() => setIsLangOpen((p) => !p)}
                className="clay-btn flex items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-[11px] font-extrabold text-slate-700 transition-all hover:text-slate-950 dark:text-slate-200 dark:hover:text-white"
                aria-label="Select language"
              >
                <span>
                  {LANGUAGES.find((l) => l.value === selectedLang)?.short ??
                    selectedLang.toUpperCase()}
                </span>
                <ChevronDown
                  className={cn(
                    'size-3 stroke-[2.5] text-slate-400 transition-transform duration-150',
                    isLangOpen && 'rotate-180'
                  )}
                />
              </button>
              {isLangOpen && (
                <div className="clay-card absolute right-0 z-50 mt-1.5 flex min-w-[130px] flex-col gap-0.5 p-1.5 shadow-xl">
                  {LANGUAGES.map((l) => (
                    <button
                      key={l.value}
                      onClick={() => {
                        setSelectedLang(l.value);
                        setIsLangOpen(false);
                      }}
                      className={cn(
                        'rounded-lg px-2.5 py-1.5 text-left text-xs font-bold transition-all',
                        l.value === selectedLang
                          ? 'clay-btn-primary text-slate-950'
                          : 'text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800'
                      )}
                    >
                      {l.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* 2. Theme Toggle */}
            <button
              onClick={() => setTheme(isDark ? 'light' : 'dark')}
              className="clay-btn flex size-8.5 items-center justify-center rounded-xl text-slate-700 transition-transform hover:scale-105 hover:text-slate-950 active:scale-95"
              title={isDark ? 'Switch to Light mode' : 'Switch to Dark mode'}
              aria-label="Toggle theme"
            >
              {mounted && isDark ? (
                <Sun className="size-4 stroke-[2.5] text-amber-500" />
              ) : (
                <Moon className="size-4 stroke-[2.5] text-indigo-600" />
              )}
            </button>

            {/* 3. View GitHub */}
            <a
              href="https://github.com/ace-ify/murf-livekit"
              target="_blank"
              rel="noopener noreferrer"
              className="clay-btn flex size-8.5 items-center justify-center rounded-xl text-slate-700 transition-transform hover:scale-105 hover:text-slate-950 active:scale-95"
              title="View on GitHub"
              aria-label="View on GitHub"
            >
              <svg
                className="size-4 fill-current"
                viewBox="0 0 24 24"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
            </a>
          </div>
        </header>

        {/* ── Microphone Error Warning Banner ────────────────────────────────── */}
        <AnimatePresence>
          {isDenied && (
            <motion.div
              initial={{ height: 0, opacity: 0, marginBottom: 0 }}
              animate={{ height: 'auto', opacity: 1, marginBottom: 12 }}
              exit={{ height: 0, opacity: 0, marginBottom: 0 }}
              className="clay-card overflow-hidden border-amber-200/80 bg-amber-50/95 px-5 py-3"
            >
              <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 text-xs font-bold text-amber-950">
                <div className="flex items-center gap-2.5">
                  <AlertCircle className="size-5 shrink-0 text-amber-600" />
                  <span>
                    MICROPHONE BLOCKED: Samar cannot hear your voice until microphone permissions
                    are enabled.
                  </span>
                </div>
                <button
                  onClick={() => {
                    navigator.mediaDevices?.getUserMedia({ audio: true }).catch(() => {});
                  }}
                  className="clay-btn shrink-0 bg-white px-4 py-1 text-xs font-black text-amber-900"
                >
                  RETRY ACCESS
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Backend Agent / Connection Error Notification Banner ──────────── */}
        <AnimatePresence>
          {agentState === 'failed' && (
            <motion.div
              initial={{ height: 0, opacity: 0, marginBottom: 0 }}
              animate={{ height: 'auto', opacity: 1, marginBottom: 12 }}
              exit={{ height: 0, opacity: 0, marginBottom: 0 }}
              className="clay-card overflow-hidden border-rose-200/80 bg-rose-50/95 px-5 py-3"
            >
              <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 text-xs font-bold text-rose-950">
                <div className="flex items-center gap-2.5">
                  <AlertCircle className="size-5 shrink-0 text-rose-600" />
                  <span>
                    CONNECTION FAILED: Voice helpline backend is unreachable. Make sure the Python
                    agent server (`python src/agent.py dev`) is running.
                  </span>
                </div>
                <button
                  onClick={() => start()}
                  className="clay-btn shrink-0 bg-white px-4 py-1 text-xs font-black text-rose-900"
                >
                  RECONNECT
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Main Workspace Grid ────────────────────────────────────────────── */}
        <main className="flex flex-1 gap-3 overflow-hidden">
          {activeNav === 'dashboard' ? (
            <CallAnalyticsDashboard />
          ) : (
            <>
          {/* ── ALL-IN-ONE MAIN BOX (Combined Stage: Sine Wave + Fading Chat Stream + Controls) ── */}
          <section className="clay-card flex flex-1 flex-col overflow-hidden p-4 md:p-5">
            {/* Box Top Header: Status Badges & Network Latency Pill */}
            <div className="flex shrink-0 items-center justify-between border-b border-slate-200/60 pb-3 dark:border-slate-800/80">
              <div className="flex items-center gap-2.5">
                <div
                  className={cn(
                    'clay-pill flex items-center gap-2 px-3.5 py-1 text-xs font-black tracking-wide uppercase shadow-sm',
                    stateMeta.pillBg,
                    stateMeta.textColor
                  )}
                >
                  <span className={cn('size-2 rounded-full', stateMeta.dotBg)} />
                  <span>{stateMeta.badgeText}</span>
                </div>

                {/* Connecting / Low-bandwidth Adaptive Pipeline Notice */}
                {dashState === 'connecting' && (
                  <div className="clay-pill flex animate-pulse items-center gap-1.5 bg-amber-50 px-3 py-1 text-[11px] font-bold text-amber-900 shadow-sm dark:bg-amber-950/60 dark:text-amber-200">
                    <Loader2 className="size-3 animate-spin text-amber-600 dark:text-amber-400" />
                    <span>Connecting voice pipeline (Opus 48kHz)... Please wait</span>
                  </div>
                )}
              </div>

              {/* Network & Latency Pill */}
              <div className="flex items-center gap-2">
                <div
                  className={cn(
                    'clay-pill flex items-center gap-2 px-3 py-1 font-mono text-[11px] font-bold shadow-sm transition-all',
                    isConnected
                      ? 'border border-emerald-300/60 bg-emerald-50/90 text-emerald-950 dark:border-emerald-700/50 dark:bg-emerald-950/60 dark:text-emerald-300'
                      : 'bg-white/90 text-slate-700 dark:bg-[#151e2b] dark:text-slate-200'
                  )}
                  title={
                    isConnected
                      ? `Live Call Active${livePing !== null ? `: ${livePing}ms` : ''}`
                      : 'Agent Online: Ready to connect'
                  }
                >
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        'size-2 rounded-full',
                        isConnected ? 'animate-pulse bg-emerald-500' : 'bg-emerald-500'
                      )}
                    />
                    <Signal
                      className={cn(
                        'size-3.5 stroke-[2.5]',
                        isConnected
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-emerald-600 dark:text-emerald-400'
                      )}
                    />
                  </div>

                  {isConnected ? (
                    <>
                      {livePing !== null && (
                        <>
                          <span className="font-mono text-xs font-black tracking-tight text-slate-900 dark:text-white">
                            {livePing}ms
                          </span>
                          <span className="text-slate-300 dark:text-slate-600">•</span>
                        </>
                      )}
                      <span className="text-[10px] font-extrabold tracking-wider text-emerald-700 uppercase dark:text-emerald-400">
                        LIVE
                      </span>
                    </>
                  ) : (
                    <span className="text-[10px] font-extrabold tracking-wider text-slate-800 uppercase dark:text-slate-200">
                      ONLINE
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* ── SINGLE UNIFIED STAGE (Pre-Call Centerpiece Hero OR Active Sine Wave + Chat) ── */}
            <div className="clay-inset relative my-3 flex flex-1 flex-col overflow-hidden rounded-3xl p-4">
              {dashState === 'ready' || dashState === 'ended' ? (
                /* ── Pre-Call / Post-Call: 3D Spline Orb ── */
                <div
                  onClick={() => start()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      start();
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  title="Click to start consultation"
                  className="group relative flex flex-1 cursor-pointer flex-col items-center justify-center overflow-hidden rounded-3xl bg-[#E8EEF5] transition-all hover:brightness-[1.02] focus:outline-none active:scale-[0.99] dark:border dark:border-white/5 dark:bg-[#070b13]"
                >
                  <div className="pointer-events-none absolute inset-0 hidden items-center justify-center dark:flex">
                    <div className="size-80 rounded-full bg-gradient-to-tr from-cyan-500/20 via-sky-500/15 to-indigo-500/20 blur-3xl" />
                  </div>
                  <spline-viewer
                    url="https://prod.spline.design/4iC321jWjBLbdcUS/scene.splinecode"
                    className="absolute inset-0 size-full cursor-pointer"
                    style={{
                      width: '100%',
                      height: '100%',
                      transform: 'scale(1.5)',
                      transformOrigin: 'center center',
                    }}
                  />
                </div>
              ) : (
                /* ── Active / Ongoing Consultation Stage (Sine Wave + Scrolling Chat) ── */
                <>
                  {/* 1. Sine Wave Visualizer Section situated at top (30% compact ratio) */}
                  <div className="relative z-10 flex h-[100px] w-full shrink-0 items-center justify-center overflow-hidden md:h-[115px]">
                    <AgentAudioVisualizerWave
                      size="lg"
                      state={agentState ?? 'listening'}
                      color={
                        dashState === 'speaking'
                          ? '#7C3AED' // rich violet
                          : dashState === 'listening'
                            ? '#059669' // rich emerald
                            : dashState === 'connecting'
                              ? '#D97706' // amber
                              : '#0891B2' // cyan
                      }
                      colorShift={0.3}
                      lineWidth={2.5}
                      blur={0.2}
                      audioTrack={audioTrack}
                      className="size-full"
                    />
                  </div>

                  {/* 2. Chat Stream scrolling underneath, with top gradient fade-out behind the sine wave */}
                  <div className="relative mt-1 flex flex-1 flex-col overflow-hidden">
                    {/* Smooth top fade gradient overlay so messages fade away into the sine wave backdrop */}
                    <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-10 bg-gradient-to-b from-[#edf2f7] to-transparent dark:from-[#070c14]" />

                    {/* Message Scroll Container */}
                    <div className="flex flex-1 flex-col gap-3 overflow-y-auto [mask-image:linear-gradient(to_bottom,transparent_0%,black_16px,black_100%)] px-1 pt-4 pb-2">
                      {displayedMessages.map((m) => {
                        const isUser = m.isUser;
                        return (
                          <div
                            key={m.id}
                            className={cn(
                              'flex max-w-[82%] items-start gap-2.5 text-xs',
                              isUser ? 'flex-row-reverse self-end' : 'flex-row self-start'
                            )}
                          >
                            {/* Avatar / Speaker Tag */}
                            {isUser ? (
                              <span className="clay-pill shrink-0 self-start bg-slate-200 px-2.5 py-1 font-mono text-[10px] font-black text-slate-800 shadow-sm dark:bg-slate-800 dark:text-slate-200">
                                YOU
                              </span>
                            ) : (
                              <div
                                title="Careva AI"
                                className="clay-card-flat relative size-8 shrink-0 self-start overflow-hidden rounded-full border border-sky-300/80 bg-[#E8EEF5] shadow-md ring-2 ring-sky-400/30 dark:border-cyan-500/50 dark:bg-[#070b13] dark:ring-cyan-500/30"
                              >
                                <spline-viewer
                                  url="https://prod.spline.design/4iC321jWjBLbdcUS/scene.splinecode"
                                  className="pointer-events-none absolute inset-0 size-full"
                                  style={{
                                    width: '100%',
                                    height: '100%',
                                    transform: 'scale(1.8)',
                                    transformOrigin: 'center center',
                                  }}
                                />
                              </div>
                            )}

                            {/* Message Bubble */}
                            <div
                              className={cn(
                                'clay-card-flat rounded-2xl px-4 py-2.5 leading-relaxed shadow-sm transition-all',
                                isUser
                                  ? 'border border-slate-300/70 bg-white/95 text-slate-900 dark:border-slate-700/80 dark:bg-[#162030] dark:text-slate-100'
                                  : 'border border-sky-200/80 bg-gradient-to-r from-sky-50/90 to-cyan-50/90 text-slate-900 shadow-sky-100/50 dark:border-cyan-900/60 dark:bg-gradient-to-r dark:from-[#0f2133] dark:to-[#12283d] dark:text-slate-100 dark:shadow-none'
                              )}
                            >
                              <p className="whitespace-pre-wrap">{m.message}</p>
                            </div>
                          </div>
                        );
                      })}
                      <div ref={messagesEndRef} />
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* 3. Bottom Section: Integrated LiveKit Control Bar with Chat Input */}
            <div className="mt-2 flex shrink-0 flex-col gap-2">
              {/* ── Live Facility Push Card (Day 5 LiveKit Data Event) ───────────── */}
              <AnimatePresence>
                {activeFacility && (
                  <motion.div
                    initial={{ opacity: 0, y: 12, scale: 0.97 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 8, scale: 0.97 }}
                    transition={{ duration: 0.22, ease: 'easeOut' }}
                    className="clay-card relative overflow-hidden border border-emerald-300/80 bg-gradient-to-r from-emerald-50/95 via-teal-50/95 to-cyan-50/95 p-3.5 shadow-lg shadow-emerald-500/10 dark:border-emerald-700/60 dark:bg-gradient-to-r dark:from-[#0b1f1c] dark:via-[#0c2227] dark:to-[#0a1b24] dark:shadow-none"
                  >
                    {/* Top Header: Badge + Close */}
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="flex size-7 shrink-0 items-center justify-center rounded-xl bg-emerald-500 text-white shadow-sm">
                          <Building2 className="size-4" />
                        </div>
                        <div>
                          <h4 className="text-xs font-black text-slate-900 dark:text-slate-100">
                            {activeFacility.name}
                          </h4>
                          <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
                            <span className="clay-pill bg-emerald-100 px-2 py-0.5 text-[9px] font-black text-emerald-800 uppercase dark:bg-emerald-900/80 dark:text-emerald-200">
                              {activeFacility.facility_type}
                            </span>
                            <span className="flex items-center gap-1 text-[10px] font-bold text-slate-600 dark:text-slate-300">
                              <Clock className="size-3 text-emerald-600" />
                              {activeFacility.opd_timings}
                            </span>
                            {activeFacility.emergency_24x7 && (
                              <span className="clay-pill bg-rose-100 px-2 py-0.5 text-[9px] font-black text-rose-800 uppercase dark:bg-rose-900/80 dark:text-rose-200">
                                24x7 Emergency
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      <button
                        onClick={() => setActiveFacility(null)}
                        className="clay-btn flex size-6 shrink-0 items-center justify-center rounded-lg p-0 text-slate-500 hover:text-slate-800"
                        title="Dismiss card"
                      >
                        <X className="size-3.5" />
                      </button>
                    </div>

                    {/* Address & Free Services */}
                    <div className="mt-2.5 grid grid-cols-1 gap-2 text-[11px] md:grid-cols-2">
                      <div className="flex items-start gap-1.5 text-slate-700 dark:text-slate-300">
                        <MapPin className="mt-0.5 size-3.5 shrink-0 text-emerald-600" />
                        <span className="line-clamp-2">{activeFacility.address}</span>
                      </div>

                      <div className="flex flex-wrap items-center gap-1">
                        <HeartPulse className="size-3.5 shrink-0 text-teal-600" />
                        <span className="font-bold text-slate-800 dark:text-slate-200">
                          Free Services:
                        </span>
                        {activeFacility.free_services.slice(0, 2).map((srv, idx) => (
                          <span
                            key={idx}
                            className="clay-pill bg-white/80 px-1.5 py-0.5 text-[9px] font-bold text-slate-700 dark:bg-slate-800 dark:text-slate-200"
                          >
                            {srv}
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Interactive Live Map Embed */}
                    <AnimatePresence>
                      {showFacilityMap && (
                        <motion.div
                          initial={{ opacity: 0, height: 0 }}
                          animate={{ opacity: 1, height: 170 }}
                          exit={{ opacity: 0, height: 0 }}
                          transition={{ duration: 0.25 }}
                          className="relative mt-2.5 overflow-hidden rounded-xl border border-emerald-300/80 bg-slate-100 shadow-inner dark:border-emerald-700/60 dark:bg-slate-900"
                        >
                          <iframe
                            title="Facility Live Location Map"
                            width="100%"
                            height="170"
                            loading="lazy"
                            className="border-0 filter dark:contrast-90 dark:hue-rotate-180 dark:invert-[0.88]"
                            src={`https://maps.google.com/maps?q=${encodeURIComponent(
                              `${activeFacility.name}, ${activeFacility.address}`
                            )}&t=&z=14&ie=UTF8&iwloc=&output=embed`}
                          />
                        </motion.div>
                      )}
                    </AnimatePresence>

                    {/* Footer Action Links */}
                    <div className="mt-3 flex items-center justify-between border-t border-emerald-200/60 pt-2 text-[10px] dark:border-emerald-800/40">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-slate-500 dark:text-slate-400">
                          Verified: {facilityTimestamp}
                        </span>
                        <button
                          type="button"
                          onClick={() => setShowFacilityMap(!showFacilityMap)}
                          className="clay-pill flex items-center gap-1 bg-white/80 px-2 py-0.5 font-bold text-emerald-800 hover:bg-emerald-100 dark:bg-slate-800 dark:text-emerald-300"
                          title="Toggle Map View"
                        >
                          <MapIcon className="size-3" />
                          <span>{showFacilityMap ? 'HIDE MAP' : 'SHOW MAP'}</span>
                        </button>
                      </div>

                      <div className="flex items-center gap-2">
                        <a
                          href={`https://maps.google.com/?q=${encodeURIComponent(
                            `${activeFacility.name} ${activeFacility.address}`
                          )}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="clay-btn flex items-center gap-1 bg-white/90 px-2.5 py-1 font-bold text-emerald-800 hover:text-emerald-950 dark:bg-slate-800 dark:text-emerald-300"
                        >
                          <Navigation className="size-3" />
                          <span>DIRECTIONS</span>
                        </a>
                        <a
                          href="tel:108"
                          className="clay-btn-primary flex items-center gap-1 px-3 py-1 font-bold text-slate-950"
                        >
                          <Phone className="size-3 stroke-[2.5]" />
                          <span>CALL 108</span>
                        </a>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Quick Prompt Suggestion Chips */}
              <div className="flex flex-wrap items-center gap-1.5">
                <div className="mr-1 flex items-center gap-1 text-[10px] font-bold text-slate-500">
                  <Sparkles className="size-3 text-sky-500" />
                  <span>{selectedLang === 'hi' ? 'सुझाव:' : 'SUGGEST:'}</span>
                </div>
                {QUICK_SUGGESTIONS[selectedLang].map((chip: string, idx: number) => (
                  <button
                    key={idx}
                    onClick={() => handleSendMessage(chip)}
                    className="clay-pill bg-white/90 px-2.5 py-0.5 text-[10px] font-bold text-slate-700 transition-all hover:bg-sky-50 hover:text-sky-900 active:scale-95 dark:bg-slate-800/90 dark:text-slate-200 dark:hover:bg-slate-700"
                  >
                    {chip}
                  </button>
                ))}
              </div>

              {/* Input and Controls Row */}
              <div className="flex items-center gap-2">
                {/* Mute Mic Button */}
                <button
                  onClick={() => {
                    if (isConnected) microphoneToggle.toggle();
                  }}
                  disabled={!isConnected}
                  className={cn(
                    'clay-btn flex size-10 shrink-0 items-center justify-center rounded-2xl transition-all disabled:opacity-50',
                    microphoneToggle.enabled
                      ? 'text-slate-700 hover:text-slate-950'
                      : 'clay-btn-danger text-white'
                  )}
                  title={microphoneToggle.enabled ? 'Mute microphone' : 'Unmute microphone'}
                >
                  {microphoneToggle.enabled ? (
                    <Mic className="size-4 stroke-[2.5]" />
                  ) : (
                    <MicOff className="size-4 stroke-[2.5]" />
                  )}
                </button>

                {/* Text Chat Input Field */}
                <div className="clay-card-flat flex flex-1 items-center px-3.5 py-1.5">
                  <input
                    type="text"
                    value={chatInputText}
                    onChange={(e) => setChatInputText(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={
                      selectedLang === 'hi'
                        ? 'अपनी बीमारी या सवाल यहाँ लिखें... (Enter दबाएं)'
                        : 'Type your symptom or question... (Press Enter to send)'
                    }
                    className="w-full bg-transparent text-xs font-medium text-slate-800 placeholder:text-slate-400 focus:outline-none dark:text-slate-100 dark:placeholder:text-slate-500"
                  />
                </div>

                {/* Send Button */}
                <button
                  onClick={() => handleSendMessage()}
                  disabled={!chatInputText.trim() || isSendingChat}
                  className="clay-btn-primary flex size-10 shrink-0 items-center justify-center rounded-2xl disabled:scale-100 disabled:opacity-40"
                  title="Send message"
                >
                  {isSendingChat ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <SendHorizontal className="size-4 stroke-[2.5]" />
                  )}
                </button>

                {/* Start / End Call Button */}
                {!isConnected ? (
                  <button
                    onClick={() => start()}
                    className="clay-btn-primary flex items-center gap-1.5 px-4 py-2 text-xs font-black uppercase shadow-md transition-transform hover:scale-105 active:scale-95"
                    title={dashState === 'ended' ? 'Resume session' : 'Start consultation'}
                  >
                    <Phone className="size-3.5 stroke-[2.5]" />
                    <span className="hidden sm:inline">START</span>
                  </button>
                ) : (
                  <button
                    onClick={() => end()}
                    className="clay-btn-danger flex items-center gap-1.5 px-4 py-2 text-xs font-black uppercase shadow-md transition-transform hover:scale-105 active:scale-95"
                    title="End current call"
                  >
                    <PhoneOff className="size-3.5 stroke-[2.5]" />
                    <span className="hidden sm:inline">END</span>
                  </button>
                )}
              </div>
            </div>
          </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
