// Unit tests of the recorder screen with fake devices: source checks, start, pause, stop, save, gaps, interruptions and leaving.
import { Dialog } from '@angular/cdk/dialog';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { Observable, isObservable, of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Project, Recording, RecordingChunkAck } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import {
  FakeMedia,
  FakeMediaRecorder,
  FakeTrack,
  fakeStream,
  installFakeMedia,
} from './fake-media.testing';
import { RecorderComponent, defaultRecordingName, formatElapsed } from './recorder.component';

const RECORDING: Recording = {
  id: 'rec-1',
  projectId: 'project-9',
  state: 'recording',
  interrupted: false,
  storedBytes: 0,
  maxBytes: 4_294_967_296,
  createdAt: '2026-10-29T13:05:00Z',
  updatedAt: '2026-10-29T13:05:00Z',
  lastChunkAt: null,
  tracks: [],
  capture: { chunkSeconds: 4, videoBitsPerSecond: 1_500_000, maxFrameRate: 15 },
};

const SAVED: Project = {
  id: 'project-9',
  name: 'Weekly sync',
  description: null,
  mode: 'general',
  sourceLanguage: 'auto',
  glossary: [],
  detectedLanguage: null,
  status: 'uploaded',
  createdAt: '2026-10-29T13:05:00Z',
  updatedAt: '2026-10-29T13:06:00Z',
  videoAssetId: 'asset-1',
  activeJobId: null,
  recording: { id: 'rec-1', state: 'completed', interrupted: false },
};

interface SetupOptions {
  systemAudio?: boolean;
  microphone?: 'ok' | 'blocked';
  query?: Record<string, string>;
  getRecording?: ReturnType<typeof vi.fn>;
  completeRecording?: ReturnType<typeof vi.fn>;
  uploadRecordingChunk?: ReturnType<typeof vi.fn>;
  confirm?: boolean;
  supported?: boolean;
}

let media: FakeMedia | null = null;

afterEach(() => {
  media?.restore();
  media = null;
  vi.useRealTimers();
});

/** Lets promise callbacks and signal effects run. */
async function settle(fixture: { detectChanges: () => void; whenStable: () => Promise<unknown> }) {
  for (let index = 0; index < 5; index += 1) {
    await Promise.resolve();
  }
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

/** Builds the recorder with fake devices and a fake API; nothing reaches a network or a device. */
async function setup(options: SetupOptions = {}) {
  const screenVideo = new FakeTrack('video', { displaySurface: 'monitor' });
  const screenAudio = new FakeTrack('audio');
  const micTrack = new FakeTrack('audio', { deviceId: 'mic-a' });
  const screen =
    options.systemAudio === false ? fakeStream(screenVideo) : fakeStream(screenVideo, screenAudio);
  if (options.supported !== false) {
    media = installFakeMedia({
      getDisplayMedia: vi.fn(() => Promise.resolve(screen)),
      getUserMedia: vi.fn(() =>
        options.microphone === 'blocked'
          ? Promise.reject(new DOMException('denied', 'NotAllowedError'))
          : Promise.resolve(fakeStream(micTrack)),
      ),
      enumerateDevices: () =>
        Promise.resolve([
          { kind: 'audioinput', deviceId: 'mic-a', label: 'Headset', groupId: 'g' },
          { kind: 'audioinput', deviceId: 'mic-b', label: '', groupId: 'g' },
        ] as MediaDeviceInfo[]),
    });
  }
  const api = {
    createRecording: vi.fn(() => of(RECORDING)),
    uploadRecordingChunk:
      options.uploadRecordingChunk ??
      vi.fn(
        (
          _id: string,
          track: string,
          sequence: number,
          chunk: Blob,
        ): Observable<RecordingChunkAck> =>
          of({ track, sequence, sizeBytes: chunk.size, duplicate: false } as RecordingChunkAck),
      ),
    completeRecording: options.completeRecording ?? vi.fn(() => of(SAVED)),
    discardRecording: vi.fn(() => of(undefined)),
    getRecording: options.getRecording ?? vi.fn(() => of({ ...RECORDING, interrupted: true })),
    getProject: vi.fn(() => of({ ...SAVED, name: 'Recording 2026-10-29 13:05', status: 'draft' })),
  };
  const openDialog = vi.fn(() => ({ closed: of(options.confirm ?? true) }));
  await TestBed.configureTestingModule({
    imports: [RecorderComponent],
    providers: [
      provideRouter([]),
      { provide: ApiService, useValue: api },
      {
        provide: ActivatedRoute,
        useValue: { snapshot: { queryParamMap: convertToParamMap(options.query ?? {}) } },
      },
    ],
  })
    .overrideProvider(Dialog, { useValue: { open: openDialog } })
    .compileComponents();
  const router = TestBed.inject(Router);
  const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
  const fixture = TestBed.createComponent(RecorderComponent);
  fixture.detectChanges();
  const host = fixture.nativeElement as HTMLElement;
  const button = (text: string) =>
    [...host.querySelectorAll('button')].find((candidate) =>
      candidate.textContent?.trim().startsWith(text),
    ) as HTMLButtonElement | undefined;
  return {
    fixture,
    component: fixture.componentInstance,
    host,
    api,
    openDialog,
    navigate,
    button,
    screenVideo,
    screenAudio,
    micTrack,
  };
}

/** Chooses the screen and turns the microphone on through the buttons. */
async function prepareSources(ctx: Awaited<ReturnType<typeof setup>>, microphone = true) {
  ctx.button('Choose screen')?.click();
  await settle(ctx.fixture);
  if (microphone) {
    ctx.button('Use microphone')?.click();
    await settle(ctx.fixture);
  }
}

/** Starts a recording from prepared sources and returns the screen and microphone recorders. */
async function startRecording(ctx: Awaited<ReturnType<typeof setup>>, microphone = true) {
  await prepareSources(ctx, microphone);
  ctx.button('Start recording')?.click();
  await settle(ctx.fixture);
  const [screen, mic] = FakeMediaRecorder.instances;
  return { screen: screen as FakeMediaRecorder, mic: mic as FakeMediaRecorder | undefined };
}

describe('formatElapsed and defaultRecordingName', () => {
  it('formats minutes and seconds, and hours from one hour', () => {
    expect(formatElapsed(0)).toBe('00:00');
    expect(formatElapsed(65_900)).toBe('01:05');
    expect(formatElapsed(3_600_000 + 62_000)).toBe('1:01:02');
    expect(formatElapsed(-5)).toBe('00:00');
    expect(formatElapsed(Number.NaN)).toBe('00:00');
  });

  it('names a recording after the local date and time', () => {
    expect(defaultRecordingName(new Date(2026, 9, 29, 9, 5))).toBe('Recording 2026-10-29 09:05');
  });
});

describe('RecorderComponent', () => {
  it('says when the browser cannot record and disables the source buttons', async () => {
    const { host, button } = await setup({ supported: false });
    expect(host.textContent).toContain('This browser cannot record the screen');
    expect(button('Choose screen')?.disabled).toBe(true);
    expect(button('Start recording')?.disabled).toBe(true);
  });

  it('checks the screen, system audio and microphone before Start recording is offered', async () => {
    const ctx = await setup();
    const { host, button } = ctx;
    expect(host.textContent).toContain('Not chosen');
    expect(host.textContent).toContain('Choose a screen first');
    expect(host.textContent).toContain('get their consent');
    expect(host.textContent).toContain('Use a headset');
    expect(button('Start recording')?.disabled).toBe(true);

    await prepareSources(ctx);

    expect(host.textContent).toContain('Entire screen');
    expect(host.textContent).toContain('Included');
    expect(host.textContent).toContain('On');
    expect(host.querySelector('ns-segmented-gauge [role="img"]')?.getAttribute('aria-label')).toBe(
      'Microphone level 0%',
    );
    const devices = [...host.querySelectorAll('option')].map((option) =>
      option.textContent?.trim(),
    );
    expect(devices).toEqual(['Headset', 'Microphone 2']);
    expect(button('Start recording')?.disabled).toBe(false);
    expect(media?.getUserMedia).toHaveBeenCalledWith({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: false,
    });
  });

  it('explains missing system audio and needs the microphone then', async () => {
    const ctx = await setup({ systemAudio: false });
    await prepareSources(ctx, false);

    expect(ctx.host.textContent).toContain('Not shared');
    expect(ctx.host.textContent).toContain('Share system audio');
    expect(ctx.host.textContent).toContain('a recording without sound cannot be transcribed');
    expect(ctx.button('Start recording')?.disabled).toBe(true);

    ctx.button('Use microphone')?.click();
    await settle(ctx.fixture);
    expect(ctx.button('Start recording')?.disabled).toBe(false);
  });

  it('shows a blocked microphone with what to do', async () => {
    const ctx = await setup({ microphone: 'blocked' });
    await prepareSources(ctx);
    expect(ctx.host.textContent).toContain('Blocked');
    expect(ctx.host.textContent).toContain('The microphone is blocked');
    // System audio alone is enough to record.
    expect(ctx.button('Start recording')?.disabled).toBe(false);
  });

  it('opens a dated session and uploads numbered chunks of both tracks with the session settings', async () => {
    const ctx = await setup();
    const { screen, mic } = await startRecording(ctx);

    expect(ctx.api.createRecording).toHaveBeenCalledWith({
      name: expect.stringMatching(/^Recording \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/),
    });
    expect(screen.timeslice).toBe(4_000);
    expect(screen.options).toEqual({
      mimeType: 'video/webm;codecs=vp9,opus',
      videoBitsPerSecond: 1_500_000,
    });
    expect(mic?.options).toEqual({ mimeType: 'audio/webm;codecs=opus' });

    screen.emit(new Blob([new Uint8Array(1_000)], { type: 'video/webm' }));
    mic?.emit(new Blob([new Uint8Array(200)], { type: 'audio/webm' }));
    screen.emit(new Blob([new Uint8Array(500)], { type: 'video/webm' }));
    await settle(ctx.fixture);

    const calls = ctx.api.uploadRecordingChunk.mock.calls.map((call) => [
      call[0],
      call[1],
      call[2],
    ]);
    expect(calls).toEqual([
      ['rec-1', 'screen', 0],
      ['rec-1', 'microphone', 0],
      ['rec-1', 'screen', 1],
    ]);
    expect(ctx.host.textContent).toContain('1.7 KB');
    expect(ctx.host.querySelector('[role="status"]')?.textContent).toContain('Recording');
  });

  it('records the screen alone without a microphone track when the microphone is off', async () => {
    const ctx = await setup();
    const { mic } = await startRecording(ctx, false);
    expect(FakeMediaRecorder.instances).toHaveLength(1);
    expect(mic).toBeUndefined();
    expect(ctx.host.textContent).toContain('Off');
  });

  it('pauses and resumes both recorders', async () => {
    const ctx = await setup();
    const { screen, mic } = await startRecording(ctx);

    ctx.button('Pause')?.click();
    ctx.fixture.detectChanges();
    expect(screen.state).toBe('paused');
    expect(mic?.state).toBe('paused');
    expect(ctx.host.textContent).toContain('Paused');

    ctx.button('Resume')?.click();
    ctx.fixture.detectChanges();
    expect(screen.state).toBe('recording');
    expect(mic?.state).toBe('recording');
  });

  it('stops, releases the devices and saves with the typed name without starting processing', async () => {
    const ctx = await setup();
    const { screen } = await startRecording(ctx);
    screen.finalChunk = new Blob([new Uint8Array(10)], { type: 'video/webm' });

    ctx.button('Stop')?.click();
    await settle(ctx.fixture);

    expect(ctx.screenVideo.readyState).toBe('ended');
    expect(ctx.micTrack.readyState).toBe('ended');
    const input = ctx.host.querySelector('input[type="text"]') as HTMLInputElement;
    expect(input.placeholder).toMatch(/^Recording /);
    expect(ctx.host.textContent).toContain('Saving does not start processing');
    input.value = '  Weekly sync ';
    input.dispatchEvent(new Event('input'));
    ctx.button('Save')?.click();
    await settle(ctx.fixture);

    expect(ctx.api.completeRecording).toHaveBeenCalledWith('rec-1', {
      name: 'Weekly sync',
      stopAtGap: false,
    });
    expect(ctx.host.textContent).toContain('Recording saved');
    expect(ctx.host.textContent).toContain('“Weekly sync” is in Projects as Not processed');
    expect('startProcessing' in ctx.api).toBe(false);
  });

  it('keeps the date name when the name field is empty', async () => {
    const ctx = await setup();
    await startRecording(ctx);
    ctx.button('Stop')?.click();
    await settle(ctx.fixture);
    ctx.button('Save')?.click();
    await settle(ctx.fixture);
    expect(ctx.api.completeRecording).toHaveBeenCalledWith('rec-1', { stopAtGap: false });
  });

  it('offers to save what was recorded when the server reports missing parts', async () => {
    const completeRecording = vi
      .fn()
      .mockReturnValueOnce(
        throwError(
          () =>
            new NarrastepApiError(
              'RECORDING_INCOMPLETE',
              'Some parts are missing.',
              { track: 'screen', firstMissingSequence: 3, missingCount: 1 },
              409,
            ),
        ),
      )
      .mockReturnValueOnce(of(SAVED));
    const ctx = await setup({ completeRecording });
    await startRecording(ctx);
    ctx.button('Stop')?.click();
    await settle(ctx.fixture);

    ctx.button('Save')?.click();
    await settle(ctx.fixture);
    expect(ctx.host.querySelector('[role="alert"]')?.textContent).toContain(
      'Some parts are missing.',
    );
    ctx.button('Save what was recorded')?.click();
    await settle(ctx.fixture);

    expect(completeRecording).toHaveBeenLastCalledWith('rec-1', { stopAtGap: true });
    expect(ctx.host.textContent).toContain('Recording saved');
  });

  it('words a join failure for the recorder', async () => {
    const completeRecording = vi.fn(() =>
      throwError(() => new NarrastepApiError('FFMPEG_FAILED', 'Audio failed.', {}, 500)),
    );
    const ctx = await setup({ completeRecording });
    await startRecording(ctx);
    ctx.button('Stop')?.click();
    await settle(ctx.fixture);
    ctx.button('Save')?.click();
    await settle(ctx.fixture);
    expect(ctx.host.textContent).toContain('could not be joined into a video');
  });

  it('waits for the last parts before Save is enabled', async () => {
    let finish: (() => void) | null = null;
    const uploadRecordingChunk = vi.fn(
      (_id: string, track: string, sequence: number, chunk: Blob) =>
        new Observable<RecordingChunkAck>((subscriber) => {
          finish = () => {
            subscriber.next({
              track,
              sequence,
              sizeBytes: chunk.size,
              duplicate: false,
            } as RecordingChunkAck);
            subscriber.complete();
          };
        }),
    );
    const ctx = await setup({ uploadRecordingChunk });
    const { screen } = await startRecording(ctx, false);
    screen.finalChunk = new Blob([new Uint8Array(10)], { type: 'video/webm' });
    ctx.button('Stop')?.click();
    await settle(ctx.fixture);

    expect(ctx.host.textContent).toContain('Uploading the last parts: 1 left.');
    expect(ctx.button('Save')?.disabled).toBe(true);
    (finish as (() => void) | null)?.();
    await settle(ctx.fixture);
    expect(ctx.button('Save')?.disabled).toBe(false);
  });

  it('stops when the browser ends the screen share', async () => {
    const ctx = await setup();
    const { screen } = await startRecording(ctx);
    ctx.screenVideo.end();
    await settle(ctx.fixture);
    expect(screen.state).toBe('inactive');
    expect(ctx.button('Save')).toBeDefined();
  });

  it('ends the recording with a message when a part cannot be stored', async () => {
    const uploadRecordingChunk = vi.fn(() =>
      throwError(() => new NarrastepApiError('INSUFFICIENT_DISK_SPACE', 'Disk full.', {}, 507)),
    );
    const ctx = await setup({ uploadRecordingChunk });
    const { screen } = await startRecording(ctx);
    screen.emit(new Blob([new Uint8Array(10)], { type: 'video/webm' }));
    await settle(ctx.fixture);
    await settle(ctx.fixture);

    expect(screen.state).toBe('inactive');
    expect(ctx.host.textContent).toContain('Disk full.');
    expect(ctx.button('Save')?.disabled).toBe(false);
  });

  it('asks before leaving while recording and allows leaving otherwise', async () => {
    const ctx = await setup({ confirm: false });
    expect(ctx.component.canDeactivate()).toBe(true);
    const { screen } = await startRecording(ctx);

    const answer = ctx.component.canDeactivate();
    expect(isObservable(answer)).toBe(true);
    let allowed: boolean | null = null;
    (answer as Observable<boolean>).subscribe((value) => (allowed = value));
    expect(allowed).toBe(false);
    expect(screen.state).toBe('recording');
    expect(ctx.openDialog).toHaveBeenCalledTimes(1);
  });

  it('stops everything when the user confirms leaving', async () => {
    const ctx = await setup({ confirm: true });
    const { screen } = await startRecording(ctx);
    let allowed: boolean | null = null;
    (ctx.component.canDeactivate() as Observable<boolean>).subscribe((value) => (allowed = value));
    expect(allowed).toBe(true);
    expect(screen.state).toBe('inactive');
    expect(ctx.screenVideo.readyState).toBe('ended');
  });

  it('discards after confirmation and opens Projects', async () => {
    const ctx = await setup();
    await startRecording(ctx);
    ctx.button('Stop')?.click();
    await settle(ctx.fixture);
    ctx.button('Discard')?.click();
    await settle(ctx.fixture);
    expect(ctx.api.discardRecording).toHaveBeenCalledWith('rec-1');
    expect(ctx.navigate).toHaveBeenCalledWith(['/projects']);
  });

  it('opens an interrupted recording from Projects to save what was recorded', async () => {
    const ctx = await setup({ query: { recording: 'rec-1' } });
    await settle(ctx.fixture);
    expect(ctx.api.getRecording).toHaveBeenCalledWith('rec-1');
    expect(ctx.host.textContent).toContain('Recording interrupted');
    expect(ctx.host.textContent).toContain('This recording stopped before it was saved');
    const input = ctx.host.querySelector('input[type="text"]') as HTMLInputElement;
    expect(input.placeholder).toBe('Recording 2026-10-29 13:05');

    ctx.button('Save what was recorded')?.click();
    await settle(ctx.fixture);
    expect(ctx.api.completeRecording).toHaveBeenCalledWith('rec-1', { stopAtGap: true });
  });

  it('says so when an opened recording is already saved', async () => {
    const getRecording = vi.fn(() => of({ ...RECORDING, state: 'completed' }));
    const ctx = await setup({ query: { recording: 'rec-1' }, getRecording });
    await settle(ctx.fixture);
    expect(ctx.host.textContent).toContain('This recording is already saved');
    expect(ctx.button('Save')).toBeUndefined();
  });
});
