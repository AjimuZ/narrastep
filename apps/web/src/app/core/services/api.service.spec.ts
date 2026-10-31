import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DocumentGeneration, GenerationModel, ProcessingJob } from '../api.models';
import { ApiService } from './api.service';

const POLLED_JOB: ProcessingJob = {
  id: 'job-1',
  projectId: 'project-1',
  status: 'processing',
  currentStage: 'transcribing',
  progressPercent: 48,
  message: 'Transcribing locally.',
  errorCode: null,
  errorMessage: null,
  startedAt: '2026-07-15T10:01:00Z',
  completedAt: null,
  createdAt: '2026-07-15T10:01:00Z',
  version: 8,
};

describe('ApiService job monitoring', () => {
  afterEach(() => {
    FakeEventSource.instances = [];
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('falls back to resilient polling and retries SSE after a stream failure', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('EventSource', FakeEventSource);
    await TestBed.configureTestingModule({ providers: [provideHttpClient()] }).compileComponents();
    const api = TestBed.inject(ApiService);
    const getJob = vi
      .spyOn(api, 'getJob')
      .mockReturnValueOnce(throwError(() => new Error('API restarting')))
      .mockReturnValue(of(POLLED_JOB));
    const received: ProcessingJob[] = [];

    const subscription = api.monitorJob(POLLED_JOB.id).subscribe((job) => received.push(job));
    const firstStream = FakeEventSource.instances[0]!;
    firstStream.fail();
    await vi.advanceTimersByTimeAsync(0);

    expect(getJob).toHaveBeenCalledTimes(1);
    expect(received).toEqual([]);

    await vi.advanceTimersByTimeAsync(2_000);

    expect(getJob).toHaveBeenCalledTimes(2);
    expect(received.at(-1)).toEqual(POLLED_JOB);

    await vi.advanceTimersByTimeAsync(8_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    const retryStream = FakeEventSource.instances[1]!;
    retryStream.open();
    const callsWhenStreamRecovered = getJob.mock.calls.length;

    await vi.advanceTimersByTimeAsync(4_000);

    expect(getJob).toHaveBeenCalledTimes(callsWhenStreamRecovered);
    expect(firstStream.close).toHaveBeenCalled();
    subscription.unsubscribe();
    expect(retryStream.close).toHaveBeenCalled();
  });
});

const MODEL: GenerationModel = {
  id: 'gemma-4-e2b',
  label: 'Light',
  state: 'not_installed',
  downloadedBytes: 0,
  totalBytes: 3_398_849_248,
  files: [{ name: 'gemma-4-E2B-it-Q4_0.gguf', sizeBytes: 2_841_481_184 }],
  repository: 'ggml-org/gemma-4-E2B-it-GGUF',
  revision: '6938c58e698502037401a5f19f6905f2fb2f7dee',
  licenseName: 'Apache License 2.0',
  licenseUrl: 'https://ai.google.dev/gemma/docs/gemma_4_license',
  errorMessage: null,
  updatedAt: null,
};

describe('ApiService model calls', () => {
  afterEach(() => vi.useRealTimers());

  async function setup() {
    await TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    return { api: TestBed.inject(ApiService), http: TestBed.inject(HttpTestingController) };
  }

  it('sends each lifecycle action to its documented endpoint', async () => {
    const { api, http } = await setup();
    const calls: [() => unknown, string, string][] = [
      [() => api.listModels(), 'GET', '/api/v1/models'],
      [() => api.getModel('gemma-4-e2b'), 'GET', '/api/v1/models/gemma-4-e2b'],
      [() => api.downloadModel('gemma-4-e2b'), 'POST', '/api/v1/models/gemma-4-e2b/download'],
      [() => api.cancelModelDownload('gemma-4-e2b'), 'POST', '/api/v1/models/gemma-4-e2b/cancel'],
      [() => api.enableModel('gemma-4-e2b'), 'POST', '/api/v1/models/gemma-4-e2b/enable'],
      [() => api.disableModel('gemma-4-e2b'), 'POST', '/api/v1/models/gemma-4-e2b/disable'],
      [() => api.deleteModel('gemma-4-e2b'), 'DELETE', '/api/v1/models/gemma-4-e2b'],
    ];

    for (const [call, method, url] of calls) {
      (call() as { subscribe: () => void }).subscribe();
      const request = http.expectOne(url);
      expect(request.request.method).toBe(method);
      request.flush(MODEL);
    }
    http.verify();
  });

  it('polls every two seconds and ends with the first state that is not in progress', async () => {
    vi.useFakeTimers();
    const { api } = await setup();
    const getModel = vi
      .spyOn(api, 'getModel')
      .mockReturnValueOnce(of({ ...MODEL, state: 'downloading', downloadedBytes: 10 }))
      .mockReturnValueOnce(throwError(() => new Error('API restarting')))
      .mockReturnValueOnce(of({ ...MODEL, state: 'verifying', downloadedBytes: 20 }))
      .mockReturnValue(of({ ...MODEL, state: 'installed', downloadedBytes: 20 }));
    const states: string[] = [];
    let completed = false;

    api.monitorModel('gemma-4-e2b').subscribe({
      next: (model) => states.push(model.state),
      complete: () => (completed = true),
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(states).toEqual(['downloading']);

    // The failed poll is skipped and the next one still happens.
    await vi.advanceTimersByTimeAsync(4_000);
    expect(states).toEqual(['downloading', 'verifying']);
    expect(completed).toBe(false);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(states).toEqual(['downloading', 'verifying', 'installed']);
    expect(completed).toBe(true);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(getModel).toHaveBeenCalledTimes(4);
  });
});

const GENERATION: DocumentGeneration = {
  id: 'generation-1',
  projectId: 'project-1',
  modelId: 'gemma-4-e2b',
  status: 'queued',
  currentStage: 'queued',
  progressPercent: 0,
  message: 'Waiting for the local worker.',
  partsTotal: 0,
  partsCompleted: 0,
  language: null,
  promptVersion: 'generation-prompts-v1',
  documentAvailable: false,
  sizeBytes: null,
  errorCode: null,
  errorMessage: null,
  version: 1,
  createdAt: '2026-07-15T10:00:00Z',
  startedAt: null,
  completedAt: null,
};

describe('ApiService generation calls', () => {
  afterEach(() => vi.useRealTimers());

  async function setup() {
    await TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    return { api: TestBed.inject(ApiService), http: TestBed.inject(HttpTestingController) };
  }

  it('sends each generation action to its documented endpoint', async () => {
    const { api, http } = await setup();
    const calls: [() => unknown, string, string][] = [
      [() => api.createGeneration('project-1'), 'POST', '/api/v1/projects/project-1/generations'],
      [() => api.getGeneration('generation-1'), 'GET', '/api/v1/generations/generation-1'],
      [
        () => api.cancelGeneration('generation-1'),
        'POST',
        '/api/v1/generations/generation-1/cancel',
      ],
    ];

    for (const [call, method, url] of calls) {
      (call() as { subscribe: () => void }).subscribe();
      const request = http.expectOne(url);
      expect(request.request.method).toBe(method);
      request.flush(GENERATION);
    }
    http.verify();
  });

  it('unwraps the generation list and reads the document as text', async () => {
    const { api, http } = await setup();
    let listed: DocumentGeneration[] = [];
    let text = '';

    api.listGenerations('project-1').subscribe((items) => (listed = items));
    http
      .expectOne('/api/v1/projects/project-1/generations')
      .flush({ items: [GENERATION], total: 1 });
    api.getGenerationDocument('generation-1').subscribe((body) => (text = body));
    const request = http.expectOne('/api/v1/generations/generation-1/document');
    expect(request.request.responseType).toBe('text');
    request.flush('# Meeting\n');

    expect(listed).toEqual([GENERATION]);
    expect(text).toBe('# Meeting\n');
    http.verify();
  });

  it('polls every two seconds and ends with the first final status', async () => {
    vi.useFakeTimers();
    const { api } = await setup();
    const getGeneration = vi
      .spyOn(api, 'getGeneration')
      .mockReturnValueOnce(of({ ...GENERATION, status: 'processing', partsTotal: 3 }))
      .mockReturnValueOnce(throwError(() => new Error('API restarting')))
      .mockReturnValueOnce(
        of({ ...GENERATION, status: 'processing', partsTotal: 3, partsCompleted: 1 }),
      )
      .mockReturnValue(of({ ...GENERATION, status: 'completed', partsTotal: 3 }));
    const seen: string[] = [];
    let completed = false;

    api.monitorGeneration('generation-1').subscribe({
      next: (generation) => seen.push(`${generation.status}:${generation.partsCompleted}`),
      complete: () => (completed = true),
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual(['processing:0']);

    // The failed poll is skipped and the next one still happens.
    await vi.advanceTimersByTimeAsync(4_000);
    expect(seen).toEqual(['processing:0', 'processing:1']);
    expect(completed).toBe(false);

    await vi.advanceTimersByTimeAsync(2_000);
    expect(seen).toEqual(['processing:0', 'processing:1', 'completed:0']);
    expect(completed).toBe(true);

    await vi.advanceTimersByTimeAsync(10_000);
    expect(getGeneration).toHaveBeenCalledTimes(4);
  });
});

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly close = vi.fn();
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private readonly listeners = new Map<string, EventListener[]>();

  /** Records each in-memory connection so a test can drive its transport lifecycle. */
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  /** Registers named SSE listeners using the same contract as the browser implementation. */
  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  /** Simulates a browser transport failure without completing the monitor observable. */
  fail(): void {
    this.onerror?.(new Event('error'));
  }

  /** Simulates a successful SSE retry so the service can stop fallback polling. */
  open(): void {
    this.onopen?.(new Event('open'));
  }
}
