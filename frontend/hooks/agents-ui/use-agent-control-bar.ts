import { useCallback } from 'react';
import { Track } from 'livekit-client';
import {
  type TrackReference,
  usePersistentUserChoices,
  useSessionContext,
  useTrackToggle,
} from '@livekit/components-react';

export interface UseInputControlsProps {
  saveUserChoices?: boolean;
  onDeviceError?: (error: { source: Track.Source; error: Error }) => void;
}

export interface UseInputControlsReturn {
  microphoneTrack?: TrackReference;
  microphoneToggle: ReturnType<typeof useTrackToggle<Track.Source.Microphone>>;
}

export function useInputControls({
  saveUserChoices = true,
  onDeviceError,
}: UseInputControlsProps = {}): UseInputControlsReturn {
  const {
    local: { microphoneTrack },
  } = useSessionContext();

  const microphoneToggle = useTrackToggle({
    source: Track.Source.Microphone,
    onDeviceError: (error) => onDeviceError?.({ source: Track.Source.Microphone, error }),
  });

  const { saveAudioInputEnabled } = usePersistentUserChoices({ preventSave: !saveUserChoices });

  const handleToggleMicrophone = useCallback(
    async (enabled?: boolean) => {
      await microphoneToggle.toggle(enabled);
      saveAudioInputEnabled(!microphoneToggle.enabled);
    },
    [microphoneToggle, saveAudioInputEnabled]
  );

  return {
    microphoneTrack,
    microphoneToggle: {
      ...microphoneToggle,
      toggle: handleToggleMicrophone,
    },
  };
}
