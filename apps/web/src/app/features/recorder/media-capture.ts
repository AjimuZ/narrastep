// Browser capture helpers for the recorder: screen and microphone requests, recorder formats and the microphone level.

/** What the user shared, from the video track's `displaySurface` setting. */
export type ScreenSourceKind = 'monitor' | 'window' | 'browser' | 'unknown';

/** Screen track formats in order of preference: VP9, then VP8 (docs/RECORDING.md). */
export const SCREEN_MIME_TYPES_WITH_AUDIO = [
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm',
] as const;
export const SCREEN_MIME_TYPES_VIDEO_ONLY = [
  'video/webm;codecs=vp9',
  'video/webm;codecs=vp8',
  'video/webm',
] as const;
export const MICROPHONE_MIME_TYPES = ['audio/webm;codecs=opus', 'audio/webm'] as const;

/** Frame rate requested before the session's settings are known; the server default. */
export const DEFAULT_MAX_FRAME_RATE = 15;

/** Whether this browser can capture the screen and record it; false in old or mobile browsers. */
export function captureSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getDisplayMedia === 'function' &&
    typeof MediaRecorder !== 'undefined'
  );
}

/**
 * Where the page runs when it is the Narrastep desktop window: `'windows'` (system audio comes from
 * Windows loopback capture) or `'other'` (macOS, Linux: the window has no system audio), and `null`
 * in an ordinary browser. The desktop window has no preload or bridge (ADR 0014), so this reads the
 * user agent, which Electron fills with an `Electron/` token and the host system. `userAgent`
 * defaults to the browser's own.
 */
export function desktopWindowSystem(
  userAgent: string = typeof navigator === 'undefined' ? '' : navigator.userAgent,
): 'windows' | 'other' | null {
  if (!/\bElectron\//.test(userAgent)) {
    return null;
  }
  return /\bWindows\b/.test(userAgent) ? 'windows' : 'other';
}

/**
 * First MIME type the browser can record, or null when none is supported. `isSupported` defaults
 * to `MediaRecorder.isTypeSupported`.
 */
export function pickMimeType(
  candidates: readonly string[],
  isSupported: (type: string) => boolean = (type) => MediaRecorder.isTypeSupported(type),
): string | null {
  return candidates.find((type) => isSupported(type)) ?? null;
}

/**
 * Asks the browser for a screen, window or tab with its audio (system or tab audio where the
 * browser offers it). Rejects with the browser's `DOMException`: `NotAllowedError` when the user
 * cancels the picker or the browser refuses.
 */
export function requestScreen(maxFrameRate: number): Promise<MediaStream> {
  // `systemAudio: 'include'` makes Chrome and Edge offer "Share system audio" for a whole screen;
  // other browsers ignore the unknown member.
  const options = {
    video: { frameRate: { ideal: maxFrameRate, max: maxFrameRate } },
    audio: true,
    systemAudio: 'include',
    selfBrowserSurface: 'exclude',
  } as DisplayMediaStreamOptions;
  return navigator.mediaDevices.getDisplayMedia(options);
}

/**
 * Asks for the microphone (a given device, or the default) with echo cancellation and noise
 * suppression, which the recorder needs when the call plays on speakers. Rejects with the
 * browser's `DOMException` (`NotAllowedError`, `NotFoundError`, `NotReadableError`).
 */
export function requestMicrophone(deviceId: string | null): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      echoCancellation: true,
      noiseSuppression: true,
    },
    video: false,
  });
}

/** Microphones the browser lists; labels are empty until the microphone was allowed once. */
export async function listMicrophones(): Promise<MediaDeviceInfo[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((device) => device.kind === 'audioinput');
}

/** Kind of the shared surface; `unknown` when the browser does not report it. */
export function screenSourceKind(stream: MediaStream): ScreenSourceKind {
  const surface = stream.getVideoTracks()[0]?.getSettings().displaySurface;
  return surface === 'monitor' || surface === 'window' || surface === 'browser'
    ? surface
    : 'unknown';
}

/** Stops every track of the streams, releasing the screen share and the microphone. */
export function stopStreams(...streams: (MediaStream | null)[]): void {
  for (const stream of streams) {
    stream?.getTracks().forEach((track) => track.stop());
  }
}

/** Quietest level the gauge shows above zero, in dBFS; quieter input reads as empty. */
export const LEVEL_FLOOR_DB = -60;

/**
 * Microphone level from 0 to 100 for time-domain samples in [-1, 1]: the RMS in dBFS mapped
 * linearly from `LEVEL_FLOOR_DB` (0) to 0 dBFS (100). Speech at a normal distance reads about
 * 50–75. Returns 0 for silence or no samples.
 */
export function levelPercent(samples: Float32Array): number {
  if (samples.length === 0) {
    return 0;
  }
  let sum = 0;
  for (const sample of samples) {
    sum += sample * sample;
  }
  const rms = Math.sqrt(sum / samples.length);
  if (rms <= 0) {
    return 0;
  }
  const decibels = 20 * Math.log10(rms);
  const percent = ((decibels - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB) * 100;
  return Math.round(Math.min(100, Math.max(0, percent)));
}

/** Largest drop of the shown level per reading, so short sounds stay visible for a moment. */
export const LEVEL_FALL_PER_READING = 8;

/**
 * Level to show after a reading: the new measurement when it is louder, else the previous level
 * lowered by at most `LEVEL_FALL_PER_READING` (the usual meter fall-off). Stays within 0–100.
 */
export function nextShownLevel(previous: number, measured: number): number {
  return Math.min(100, Math.max(0, measured, previous - LEVEL_FALL_PER_READING));
}

/** How often the level gauge is refreshed. */
export const LEVEL_INTERVAL_MS = 100;

/**
 * Reads the level of an audio stream about ten times a second and hands it to `onLevel`. The
 * stream is only analysed locally, never recorded or sent. Returns a function that stops the
 * readings and closes the audio context; it never throws. When the browser has no Web Audio,
 * nothing is reported and the stop function does nothing.
 */
export function watchLevel(stream: MediaStream, onLevel: (percent: number) => void): () => void {
  if (typeof AudioContext === 'undefined') {
    return () => undefined;
  }
  const context = new AudioContext();
  const analyser = context.createAnalyser();
  analyser.fftSize = 2048;
  context.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  // A page opened without a click may start its audio context suspended.
  void context.resume().catch(() => undefined);
  const timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    onLevel(levelPercent(samples));
  }, LEVEL_INTERVAL_MS);
  return () => {
    clearInterval(timer);
    void context.close().catch(() => undefined);
  };
}
