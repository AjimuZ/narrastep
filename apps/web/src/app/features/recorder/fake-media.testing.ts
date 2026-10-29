// Test doubles for the recorder unit tests: MediaRecorder, media streams and the browser capture API (jsdom has none).

/**
 * `MediaRecorder` stand-in driven by the test: `emit` delivers a chunk like a timeslice would, and
 * `stop` delivers a last chunk (when `finalChunk` is set) before the `stop` event, as browsers do.
 */
export class FakeMediaRecorder extends EventTarget {
  static readonly instances: FakeMediaRecorder[] = [];
  static supportedTypes = new Set([
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp9',
    'audio/webm;codecs=opus',
  ]);

  state: RecordingState = 'inactive';
  timeslice: number | undefined;
  finalChunk: Blob | null = null;

  constructor(
    readonly stream: MediaStream,
    readonly options: MediaRecorderOptions = {},
  ) {
    super();
    FakeMediaRecorder.instances.push(this);
  }

  /** Mirrors `MediaRecorder.isTypeSupported` with the set above. */
  static isTypeSupported(type: string): boolean {
    return FakeMediaRecorder.supportedTypes.has(type);
  }

  /** Forgets created recorders and restores the supported types between tests. */
  static reset(): void {
    FakeMediaRecorder.instances.length = 0;
    FakeMediaRecorder.supportedTypes = new Set([
      'video/webm;codecs=vp9,opus',
      'video/webm;codecs=vp9',
      'audio/webm;codecs=opus',
    ]);
  }

  start(timeslice?: number): void {
    this.timeslice = timeslice;
    this.state = 'recording';
    this.dispatchEvent(new Event('start'));
  }

  pause(): void {
    this.state = 'paused';
  }

  resume(): void {
    this.state = 'recording';
  }

  stop(): void {
    if (this.state === 'inactive') return;
    this.state = 'inactive';
    if (this.finalChunk) this.emit(this.finalChunk);
    this.dispatchEvent(new Event('stop'));
  }

  /** Delivers one chunk through `dataavailable`. */
  emit(chunk: Blob): void {
    const event = new Event('dataavailable') as Event & { data: Blob };
    event.data = chunk;
    this.dispatchEvent(event);
  }
}

/** Media track stand-in with `stop`, settings and an `ended` event the test can fire. */
export class FakeTrack extends EventTarget {
  readyState: MediaStreamTrackState = 'live';

  constructor(
    readonly kind: 'audio' | 'video',
    private readonly settings: MediaTrackSettings = {},
  ) {
    super();
  }

  getSettings(): MediaTrackSettings {
    return this.settings;
  }

  applyConstraints(): Promise<void> {
    return Promise.resolve();
  }

  stop(): void {
    this.readyState = 'ended';
  }

  /** Simulates the browser ending the track, for example "Stop sharing". */
  end(): void {
    this.readyState = 'ended';
    this.dispatchEvent(new Event('ended'));
  }
}

/** A `MediaStream` stand-in holding the given tracks. */
export function fakeStream(...tracks: FakeTrack[]): MediaStream {
  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((track) => track.kind === 'audio'),
    getVideoTracks: () => tracks.filter((track) => track.kind === 'video'),
  } as unknown as MediaStream;
}

/** Installed doubles of `navigator.mediaDevices` and `MediaRecorder`; `restore` removes them. */
export interface FakeMedia {
  getDisplayMedia: (options?: DisplayMediaStreamOptions) => Promise<MediaStream>;
  getUserMedia: (constraints?: MediaStreamConstraints) => Promise<MediaStream>;
  enumerateDevices: () => Promise<MediaDeviceInfo[]>;
  restore: () => void;
}

/**
 * Replaces `navigator.mediaDevices` and the global `MediaRecorder` with the given functions and
 * `FakeMediaRecorder`. Call `restore` in `afterEach`.
 */
export function installFakeMedia(
  devices: Omit<FakeMedia, 'restore' | 'enumerateDevices'> &
    Partial<Pick<FakeMedia, 'enumerateDevices'>>,
): FakeMedia {
  const previousDevices = Object.getOwnPropertyDescriptor(navigator, 'mediaDevices');
  const previousRecorder = (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  const mediaDevices = {
    getDisplayMedia: devices.getDisplayMedia,
    getUserMedia: devices.getUserMedia,
    enumerateDevices: devices.enumerateDevices ?? (() => Promise.resolve([])),
  };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices });
  (globalThis as { MediaRecorder?: unknown }).MediaRecorder = FakeMediaRecorder;
  FakeMediaRecorder.reset();
  return {
    ...mediaDevices,
    restore: () => {
      if (previousDevices) {
        Object.defineProperty(navigator, 'mediaDevices', previousDevices);
      } else {
        delete (navigator as { mediaDevices?: unknown }).mediaDevices;
      }
      (globalThis as { MediaRecorder?: unknown }).MediaRecorder = previousRecorder;
      FakeMediaRecorder.reset();
    },
  };
}
