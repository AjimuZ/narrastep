// Unit tests of the recording engine: start offsets, chunk numbering, ordered uploads, retries, pause, stop and abort.
import { afterEach, describe, expect, it } from 'vitest';

import { RecordingTrackName } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { FakeMediaRecorder, FakeTrack, fakeStream } from './fake-media.testing';
import {
  ChunkUploader,
  RETRY_DELAYS_MS,
  RecordingEngine,
  isRetryableUploadError,
  retryDelayMs,
} from './recording-engine';

interface UploadCall {
  track: RecordingTrackName;
  sequence: number;
  chunk: Blob;
  offset: number;
  resolve: (bytes: number) => void;
  reject: (error: unknown) => void;
}

/** Builds an engine over a screen and a microphone track with a test clock and manual uploads. */
function setup(options: { tracks?: RecordingTrackName[] } = {}) {
  let now = 1_000;
  const calls: UploadCall[] = [];
  const sleeps: number[] = [];
  const upload: ChunkUploader = (track, sequence, chunk, offset) =>
    new Promise<number>((resolve, reject) => {
      calls.push({ track, sequence, chunk, offset, resolve, reject });
    });
  const tracks = (options.tracks ?? ['screen', 'microphone']).map((track) => ({
    track,
    stream: fakeStream(new FakeTrack(track === 'screen' ? 'video' : 'audio')),
    mimeType: track === 'screen' ? 'video/webm;codecs=vp9,opus' : 'audio/webm;codecs=opus',
    ...(track === 'screen' ? { videoBitsPerSecond: 2_500_000 } : {}),
  }));
  const engine = new RecordingEngine(tracks, 5_000, upload, {
    createRecorder: (stream, recorderOptions) =>
      new FakeMediaRecorder(stream, recorderOptions) as unknown as MediaRecorder,
    now: () => now,
    sleep: (ms) => {
      sleeps.push(ms);
      return Promise.resolve();
    },
  });
  const [screen, microphone] = FakeMediaRecorder.instances.slice(-tracks.length);
  return {
    engine,
    calls,
    sleeps,
    screen: screen as FakeMediaRecorder,
    microphone: microphone as FakeMediaRecorder,
    advance: (ms: number) => {
      now += ms;
    },
  };
}

/** Lets pending promise callbacks run. */
async function flush(): Promise<void> {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve();
  }
}

const chunk = (size: number, type = 'video/webm') => new Blob([new Uint8Array(size)], { type });

describe('RecordingEngine', () => {
  afterEach(() => FakeMediaRecorder.reset());

  it('starts both recorders with the chunk length, format and bitrate', () => {
    const { engine, screen, microphone } = setup();

    engine.start();

    expect(engine.state()).toBe('recording');
    expect(screen.state).toBe('recording');
    expect(screen.timeslice).toBe(5_000);
    expect(screen.options).toEqual({
      mimeType: 'video/webm;codecs=vp9,opus',
      videoBitsPerSecond: 2_500_000,
    });
    expect(microphone.timeslice).toBe(5_000);
    expect(microphone.options).toEqual({ mimeType: 'audio/webm;codecs=opus' });
  });

  it('refuses to be built without a track', () => {
    expect(() => new RecordingEngine([], 5_000, () => Promise.resolve(0))).toThrow();
  });

  it('numbers chunks per track from 0, skips empty ones and sends each track start offset', async () => {
    const { engine, screen, microphone, calls, advance } = setup();
    // The microphone recorder reports its start 40 ms after the common start.
    microphone.start = function (timeslice?: number) {
      advance(40);
      FakeMediaRecorder.prototype.start.call(this, timeslice);
    };
    engine.start();

    screen.emit(chunk(100));
    microphone.emit(chunk(0, 'audio/webm'));
    microphone.emit(chunk(10, 'audio/webm'));
    await flush();

    expect(calls.map(({ track, sequence, offset }) => [track, sequence, offset])).toEqual([
      ['screen', 0, 0],
      ['microphone', 0, 40],
    ]);
    calls[0]?.resolve(100);
    calls[1]?.resolve(10);
    await flush();
    expect(engine.uploadedBytes()).toBe(110);
    expect(engine.pendingChunks()).toBe(0);
  });

  it('uploads one chunk per track at a time, oldest first, keeping later chunks in memory', async () => {
    const { engine, screen, calls } = setup({ tracks: ['screen'] });
    engine.start();

    screen.emit(chunk(1));
    screen.emit(chunk(2));
    screen.emit(chunk(3));
    await flush();

    expect(calls).toHaveLength(1);
    expect(engine.pendingChunks()).toBe(3);
    calls[0]?.resolve(1);
    await flush();
    expect(calls.map((call) => call.sequence)).toEqual([0, 1]);
    calls[1]?.resolve(2);
    await flush();
    calls[2]?.resolve(3);
    await flush();
    expect(calls.map((call) => call.sequence)).toEqual([0, 1, 2]);
    expect(engine.pendingChunks()).toBe(0);
    expect(engine.uploadedBytes()).toBe(6);
  });

  it('retries a chunk after a network failure with growing delays and the same sequence', async () => {
    const { engine, screen, calls, sleeps } = setup({ tracks: ['screen'] });
    engine.start();
    screen.emit(chunk(5));
    await flush();

    calls[0]?.reject(new NarrastepApiError('REQUEST_FAILED', 'offline', {}, 0));
    await flush();
    expect(engine.retrying()).toBe(true);
    calls[1]?.reject(new NarrastepApiError('INTERNAL_ERROR', 'restart', {}, 503));
    await flush();
    calls[2]?.resolve(5);
    await flush();

    expect(calls.map((call) => call.sequence)).toEqual([0, 0, 0]);
    expect(sleeps).toEqual([RETRY_DELAYS_MS[0], RETRY_DELAYS_MS[1]]);
    expect(engine.retrying()).toBe(false);
    expect(engine.pendingChunks()).toBe(0);
    expect(engine.fatalError()).toBeNull();
  });

  it('stops recording and drops waiting chunks after a failure that retrying cannot fix', async () => {
    const { engine, screen, microphone, calls } = setup();
    engine.start();
    screen.emit(chunk(5));
    screen.emit(chunk(6));
    await flush();

    const full = new NarrastepApiError('INSUFFICIENT_DISK_SPACE', 'full', {}, 507);
    calls[0]?.reject(full);
    await flush();

    expect(engine.fatalError()).toBe(full);
    expect(engine.pendingChunks()).toBe(0);
    expect(screen.state).toBe('inactive');
    expect(microphone.state).toBe('inactive');
    expect(engine.state()).toBe('stopped');
    // Chunks delivered after the failure are not queued.
    screen.emit(chunk(7));
    expect(engine.pendingChunks()).toBe(0);
    expect(calls).toHaveLength(1);
  });

  it('pauses and resumes both recorders and leaves paused time out of the length', () => {
    const { engine, screen, microphone, advance } = setup();
    engine.start();
    advance(3_000);
    engine.pause();
    expect(engine.state()).toBe('paused');
    expect(screen.state).toBe('paused');
    expect(microphone.state).toBe('paused');
    advance(10_000);
    expect(engine.elapsedMs()).toBe(3_000);

    engine.resume();
    advance(2_000);

    expect(screen.state).toBe('recording');
    expect(microphone.state).toBe('recording');
    expect(engine.elapsedMs()).toBe(5_000);
  });

  it('resolves stop after the last chunks and keeps uploading them', async () => {
    const { engine, screen, microphone, calls, advance } = setup();
    engine.start();
    advance(7_000);
    engine.pause();
    advance(1_000);
    screen.finalChunk = chunk(9);
    microphone.finalChunk = chunk(4, 'audio/webm');

    await engine.stop();

    expect(engine.state()).toBe('stopped');
    expect(engine.elapsedMs()).toBe(7_000);
    expect(engine.busy()).toBe(true);
    expect(calls.map(({ track, sequence }) => [track, sequence])).toEqual([
      ['screen', 0],
      ['microphone', 0],
    ]);
    calls.forEach((call) => call.resolve(call.chunk.size));
    await flush();
    expect(engine.busy()).toBe(false);
    expect(engine.uploadedBytes()).toBe(13);
  });

  it('abort stops the recorders and gives up chunks not yet uploaded', async () => {
    const { engine, screen, calls } = setup();
    engine.start();
    screen.emit(chunk(1));
    screen.emit(chunk(2));
    await flush();

    engine.abort();
    calls[0]?.resolve(1);
    await flush();

    expect(engine.state()).toBe('stopped');
    expect(engine.pendingChunks()).toBe(0);
    expect(engine.busy()).toBe(false);
    expect(calls).toHaveLength(1);
    expect(screen.state).toBe('inactive');
  });

  it('reports a recorder error as fatal', async () => {
    const { engine, screen } = setup();
    engine.start();
    const failure = new Error('encoder');
    const event = new Event('error') as Event & { error: unknown };
    event.error = failure;

    screen.dispatchEvent(event);
    await flush();

    expect(engine.fatalError()).toBe(failure);
    expect(engine.state()).toBe('stopped');
  });

  it('stop before start ends without waiting for a recorder', async () => {
    const { engine } = setup();
    await engine.stop();
    expect(engine.state()).toBe('stopped');
    expect(engine.elapsedMs()).toBe(0);
  });
});

describe('isRetryableUploadError', () => {
  it.each([
    [0, true],
    [408, true],
    [429, true],
    [500, true],
    [503, true],
    [507, false],
    [413, false],
    [409, false],
    [404, false],
    [422, false],
  ])('status %i retryable: %s', (status, expected) => {
    expect(isRetryableUploadError(new NarrastepApiError('X', 'x', {}, status))).toBe(expected);
  });

  it('retries an error that did not come from the API client', () => {
    expect(isRetryableUploadError(new TypeError('Failed to fetch'))).toBe(true);
  });
});

describe('retryDelayMs', () => {
  it('grows and then repeats the last delay', () => {
    expect([0, 1, 2, 3, 4, 5, 50].map(retryDelayMs)).toEqual([
      1_000, 2_000, 5_000, 10_000, 15_000, 15_000, 15_000,
    ]);
  });
});
