// Recording engine: runs the screen and microphone MediaRecorders together, numbers their chunks and uploads them with retries.
import { computed, signal } from '@angular/core';

import { RecordingTrackName } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';

/** One stream to record as one track of the session. */
export interface EngineTrack {
  track: RecordingTrackName;
  stream: MediaStream;
  /** Container and codecs passed to `MediaRecorder`; chosen with `pickMimeType`. */
  mimeType: string;
  /** Video bitrate for the screen track; omitted for audio-only tracks. */
  videoBitsPerSecond?: number;
}

/**
 * Stores one chunk on the server. Resolves with the stored size in bytes; rejects with the error
 * of the request (a `NarrastepApiError` from the API client) so the engine can decide whether to
 * retry. `startOffsetMs` is the track's start relative to the session's common start.
 */
export type ChunkUploader = (
  track: RecordingTrackName,
  sequence: number,
  chunk: Blob,
  startOffsetMs: number,
) => Promise<number>;

/** Dependencies replaced in unit tests: recorder construction, clock and delay. */
export interface EngineEnvironment {
  createRecorder: (stream: MediaStream, options: MediaRecorderOptions) => MediaRecorder;
  /** Monotonic milliseconds, the same clock for offsets and elapsed time. */
  now: () => number;
  /** Resolves after `ms` milliseconds. */
  sleep: (ms: number) => Promise<void>;
}

export type EngineState = 'idle' | 'recording' | 'paused' | 'stopping' | 'stopped';

/** Waits before retry `n` (0-based) of a failed chunk upload; the last value repeats. */
export const RETRY_DELAYS_MS: readonly number[] = [1_000, 2_000, 5_000, 10_000, 15_000];

const BROWSER_ENVIRONMENT: EngineEnvironment = {
  createRecorder: (stream, options) => new MediaRecorder(stream, options),
  now: () => performance.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

interface QueuedChunk {
  sequence: number;
  blob: Blob;
}

interface TrackRuntime {
  input: EngineTrack;
  recorder: MediaRecorder;
  /** Sequence the next non-empty chunk gets; counts from 0 per track. */
  nextSequence: number;
  /** Milliseconds from the common start to this recorder's `start` event; null until it fires. */
  startOffsetMs: number | null;
  /** Chunks recorded but not yet acknowledged by the server, oldest first. */
  queue: QueuedChunk[];
  uploading: boolean;
  /** True after an upload of this track failed, until one succeeds again. */
  failing: boolean;
  /** True once `recorder.start` returned; only such a recorder will fire `stop`. */
  started: boolean;
  stopped: Promise<void>;
}

/**
 * Whether a failed upload may succeed when sent again: no answer (network or API restart), a
 * timeout, rate limiting or a server error. Full disk (507), a too-large session (413), a
 * conflicting chunk (409) or a missing session (404) cannot heal by retrying.
 */
export function isRetryableUploadError(error: unknown): boolean {
  if (!(error instanceof NarrastepApiError)) {
    return true;
  }
  const status = error.status;
  if (status === 0 || status === 408 || status === 429) {
    return true;
  }
  return status >= 500 && status !== 507;
}

/**
 * Records one session from up to two streams. Both `MediaRecorder` instances start, pause, resume
 * and stop together; each emits a chunk every `chunkSeconds`. Chunks are numbered per track from 0,
 * kept in memory until the server acknowledges them, and uploaded one at a time per track (both
 * tracks in parallel). A retryable failure is retried after `RETRY_DELAYS_MS` for as long as the
 * engine lives; any other failure is stored in `fatalError`, drops the waiting chunks and stops the
 * recorders, because later chunks could not be stored either.
 *
 * Each track's start offset is the time from the moment `start` is called to that recorder's
 * `start` event, so the server can align the microphone to the screen (docs/RECORDING.md).
 */
export class RecordingEngine {
  private readonly runtimes: TrackRuntime[] = [];
  private readonly env: EngineEnvironment;
  private readonly stateSignal = signal<EngineState>('idle');
  private readonly uploadedBytesSignal = signal(0);
  private readonly pendingSignal = signal(0);
  private readonly retryingSignal = signal(false);
  private readonly fatalErrorSignal = signal<unknown>(null);
  private commonStart = 0;
  private pausedAt: number | null = null;
  private pausedTotal = 0;
  private stoppedAt: number | null = null;
  private aborted = false;
  private everStarted = false;

  /** Current engine state; `stopped` once every recorder has delivered its last chunk. */
  readonly state = this.stateSignal.asReadonly();
  /** Bytes the server acknowledged so far, both tracks together. */
  readonly uploadedBytes = this.uploadedBytesSignal.asReadonly();
  /** Chunks recorded but not yet acknowledged, both tracks together. */
  readonly pendingChunks = this.pendingSignal.asReadonly();
  /** True while at least one chunk failed and waits for its next attempt. */
  readonly retrying = this.retryingSignal.asReadonly();
  /** The first non-retryable upload or recorder failure; null while everything works. */
  readonly fatalError = this.fatalErrorSignal.asReadonly();
  /** True while recorders run or chunks still wait for the server. */
  readonly busy = computed(
    () =>
      this.state() === 'recording' ||
      this.state() === 'paused' ||
      this.state() === 'stopping' ||
      this.pendingChunks() > 0,
  );

  /**
   * Prepares one recorder per track. Requires at least one track; the `MediaRecorder` constructor
   * throws (for example `NotSupportedError` for an unknown MIME type) before anything starts.
   */
  constructor(
    tracks: readonly EngineTrack[],
    private readonly chunkMs: number,
    private readonly upload: ChunkUploader,
    environment: Partial<EngineEnvironment> = {},
  ) {
    if (tracks.length === 0) {
      throw new Error('A recording needs at least one track.');
    }
    this.env = { ...BROWSER_ENVIRONMENT, ...environment };
    for (const input of tracks) {
      const options: MediaRecorderOptions = { mimeType: input.mimeType };
      if (input.videoBitsPerSecond !== undefined) {
        options.videoBitsPerSecond = input.videoBitsPerSecond;
      }
      const recorder = this.env.createRecorder(input.stream, options);
      const runtime: TrackRuntime = {
        input,
        recorder,
        nextSequence: 0,
        startOffsetMs: null,
        queue: [],
        uploading: false,
        failing: false,
        started: false,
        stopped: new Promise<void>((resolve) => recorder.addEventListener('stop', () => resolve())),
      };
      recorder.addEventListener('start', () => {
        runtime.startOffsetMs = Math.max(0, Math.round(this.env.now() - this.commonStart));
      });
      recorder.addEventListener('dataavailable', (event) =>
        this.enqueue(runtime, (event as BlobEvent).data),
      );
      recorder.addEventListener('error', (event) => this.fail(errorOf(event)));
      this.runtimes.push(runtime);
    }
  }

  /** Starts every recorder at once. Allowed only from `idle`; a recorder that throws stops all. */
  start(): void {
    if (this.stateSignal() !== 'idle') {
      return;
    }
    this.commonStart = this.env.now();
    this.everStarted = true;
    this.stateSignal.set('recording');
    try {
      for (const runtime of this.runtimes) {
        runtime.recorder.start(this.chunkMs);
        runtime.started = true;
      }
    } catch (error) {
      this.fail(error);
    }
  }

  /** Pauses every recorder; elapsed time stops counting. No-op unless recording. */
  pause(): void {
    if (this.stateSignal() !== 'recording') {
      return;
    }
    for (const runtime of this.runtimes) {
      if (runtime.recorder.state === 'recording') {
        runtime.recorder.pause();
      }
    }
    this.pausedAt = this.env.now();
    this.stateSignal.set('paused');
  }

  /** Resumes every recorder after `pause`. No-op unless paused. */
  resume(): void {
    if (this.stateSignal() !== 'paused') {
      return;
    }
    for (const runtime of this.runtimes) {
      if (runtime.recorder.state === 'paused') {
        runtime.recorder.resume();
      }
    }
    if (this.pausedAt !== null) {
      this.pausedTotal += this.env.now() - this.pausedAt;
      this.pausedAt = null;
    }
    this.stateSignal.set('recording');
  }

  /**
   * Stops every recorder and resolves once each has delivered its last chunk (state `stopped`).
   * Uploads continue afterwards; watch `pendingChunks`. Safe to call more than once.
   */
  async stop(): Promise<void> {
    const state = this.stateSignal();
    if (state === 'idle') {
      this.stateSignal.set('stopped');
      return;
    }
    if (state === 'recording' || state === 'paused') {
      const now = this.env.now();
      if (this.pausedAt !== null) {
        this.pausedTotal += now - this.pausedAt;
        this.pausedAt = null;
      }
      this.stoppedAt = now;
      this.stateSignal.set('stopping');
      for (const runtime of this.runtimes) {
        if (runtime.recorder.state !== 'inactive') {
          runtime.recorder.stop();
        }
      }
    }
    await Promise.all(
      this.runtimes.filter((runtime) => runtime.started).map((runtime) => runtime.stopped),
    );
    this.stateSignal.set('stopped');
  }

  /**
   * Stops recording and gives up every chunk not yet uploaded, for leaving the screen. What the
   * server already stores stays there (the project shows as an interrupted recording).
   */
  abort(): void {
    this.aborted = true;
    for (const runtime of this.runtimes) {
      runtime.queue = [];
      if (runtime.recorder.state !== 'inactive') {
        runtime.recorder.stop();
      }
    }
    this.updatePending();
    this.updateRetrying();
    if (this.stateSignal() !== 'idle') {
      this.stateSignal.set('stopped');
    }
  }

  /** Recorded time in milliseconds without pauses; frozen after `stop`, 0 if never started. */
  elapsedMs(): number {
    if (!this.everStarted) {
      return 0;
    }
    const end = this.stoppedAt ?? this.pausedAt ?? this.env.now();
    return Math.max(0, end - this.commonStart - this.pausedTotal);
  }

  /** Queues a non-empty chunk with the track's next sequence and starts its upload loop. */
  private enqueue(runtime: TrackRuntime, blob: Blob | undefined): void {
    if (this.aborted || this.fatalErrorSignal() !== null || !blob || blob.size === 0) {
      return;
    }
    runtime.queue.push({ sequence: runtime.nextSequence, blob });
    runtime.nextSequence += 1;
    this.updatePending();
    void this.drain(runtime);
  }

  /** Uploads the track's queue oldest first, one request at a time, retrying recoverable failures. */
  private async drain(runtime: TrackRuntime): Promise<void> {
    if (runtime.uploading) {
      return;
    }
    runtime.uploading = true;
    let attempt = 0;
    try {
      while (!this.aborted && this.fatalErrorSignal() === null) {
        const chunk = runtime.queue[0];
        if (!chunk) {
          break;
        }
        try {
          const storedBytes = await this.upload(
            runtime.input.track,
            chunk.sequence,
            chunk.blob,
            runtime.startOffsetMs ?? 0,
          );
          runtime.queue.shift();
          attempt = 0;
          runtime.failing = false;
          this.uploadedBytesSignal.update((bytes) => bytes + storedBytes);
          this.updatePending();
          this.updateRetrying();
        } catch (error) {
          if (!isRetryableUploadError(error)) {
            this.fail(error);
            return;
          }
          runtime.failing = true;
          this.updateRetrying();
          await this.env.sleep(retryDelayMs(attempt));
          attempt += 1;
        }
      }
    } finally {
      runtime.uploading = false;
    }
  }

  /** Records the first fatal error, drops waiting chunks and stops the recorders. */
  private fail(error: unknown): void {
    if (this.fatalErrorSignal() === null) {
      this.fatalErrorSignal.set(error ?? new Error('Recording failed.'));
    }
    for (const runtime of this.runtimes) {
      runtime.queue = [];
    }
    this.updatePending();
    this.updateRetrying();
    void this.stop();
  }

  private updatePending(): void {
    this.pendingSignal.set(
      this.runtimes.reduce((total, runtime) => total + runtime.queue.length, 0),
    );
  }

  /** `retrying` is true while some track holds chunks and its last upload attempt failed. */
  private updateRetrying(): void {
    this.retryingSignal.set(
      this.runtimes.some((runtime) => runtime.failing && runtime.queue.length > 0),
    );
  }
}

/** Wait before retry `attempt` (0-based): `RETRY_DELAYS_MS`, repeating its last value. */
export function retryDelayMs(attempt: number): number {
  return RETRY_DELAYS_MS[Math.min(Math.max(0, attempt), RETRY_DELAYS_MS.length - 1)] ?? 15_000;
}

/** The error carried by a `MediaRecorder` `error` event, or the event itself. */
function errorOf(event: Event): unknown {
  return (event as Event & { error?: unknown }).error ?? event;
}
