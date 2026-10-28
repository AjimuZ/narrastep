import { provideHttpClient } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ProcessingJob } from '../api.models';
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
