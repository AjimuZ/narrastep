// Unit tests for the Processing screen: persisted progress, stage gauges, failure, cancel and retry.
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Subject, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { ProcessingJob, Project } from '../../core/api.models';
import { ApiService } from '../../core/services/api.service';
import { ProcessingComponent } from './processing.component';

const PROJECT: Project = {
  id: 'project-1',
  name: 'Claims workflow',
  description: null,
  mode: 'task_briefing',
  sourceLanguage: 'en',
  detectedLanguage: 'en',
  status: 'processing',
  createdAt: '2026-07-15T10:00:00Z',
  updatedAt: '2026-07-15T10:01:00Z',
  videoAssetId: 'asset-1',
  activeJobId: 'job-1',
};

const JOB: ProcessingJob = {
  id: 'job-1',
  projectId: 'project-1',
  status: 'processing',
  currentStage: 'transcribing',
  progressPercent: 42,
  message: 'Transcribing locally.',
  errorCode: null,
  errorMessage: null,
  startedAt: '2026-07-15T10:01:00Z',
  completedAt: null,
  createdAt: '2026-07-15T10:01:00Z',
  version: 7,
};

describe('ProcessingComponent', () => {
  it('renders persisted worker progress rather than generating browser progress', async () => {
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(JOB),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const content = fixture.nativeElement.textContent as string;
    const overall = fixture.nativeElement.querySelector(
      '.overall-gauge [role="img"]',
    ) as HTMLElement;
    expect(content).toContain('Transcribing speech');
    expect(content).toContain('Transcribing locally.');
    expect(overall.getAttribute('aria-label')).toBe('Overall progress 42%');
    expect(fixture.nativeElement.querySelector('ns-status-pill')?.textContent).toContain(
      'Processing',
    );
  });

  it('shows gauges only for finished stages and an activity line for the running stage', async () => {
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(JOB),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const rows = Array.from(
      fixture.nativeElement.querySelectorAll('.stage-list li'),
    ) as HTMLElement[];
    const row = (name: string) => rows.find((item) => item.textContent?.includes(name))!;

    const done = row('Extracting audio');
    expect(done.textContent).toContain('Done');
    expect(done.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe(
      'Extracting audio 100%',
    );

    const running = row('Transcribing speech');
    expect(running.getAttribute('aria-current')).toBe('step');
    expect(running.textContent).toContain('Running');
    expect(running.querySelector('ns-segmented-gauge')).toBeNull();
    expect(running.querySelector('[role="progressbar"]')?.getAttribute('aria-label')).toBe(
      'Transcribing speech in progress',
    );

    const waiting = row('Saving results');
    expect(waiting.textContent).toContain('Waiting');
    expect(waiting.querySelector('ns-segmented-gauge, ns-activity-line')).toBeNull();
  });

  it('presents an interrupted persisted job as retryable failure state', async () => {
    const interruptedJob: ProcessingJob = {
      ...JOB,
      status: 'interrupted',
      currentStage: 'failed',
      message: 'Processing was interrupted by a local service restart.',
      errorCode: 'PROCESSING_INTERRUPTED',
      errorMessage: 'Processing can be retried from the original source.',
      completedAt: '2026-07-15T10:02:00Z',
      version: 8,
    };
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(interruptedJob),
            startProcessing: () => of(JOB),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const content = fixture.nativeElement.textContent as string;
    expect(content).toContain('PROCESSING_INTERRUPTED');
    expect(content).toContain('Processing was interrupted.');
    expect(content).toContain('Retry processing');
    expect(fixture.nativeElement.querySelector('.stage-list [role="progressbar"]')).toBeNull();
  });

  it('renders a persisted worker failure code and safe recovery message after refresh', async () => {
    const failedJob: ProcessingJob = {
      ...JOB,
      status: 'failed',
      currentStage: 'failed',
      message: 'Local transcription failed.',
      errorCode: 'TRANSCRIPTION_MODEL_FAILED',
      errorMessage: 'The local transcription model could not process this recording.',
      completedAt: '2026-07-15T10:02:00Z',
      version: 8,
    };
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(failedJob),
            startProcessing: () => of(JOB),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const alert = fixture.nativeElement.querySelector('[role="alert"]') as HTMLElement;
    expect(alert.textContent).toContain('TRANSCRIPTION_MODEL_FAILED');
    expect(alert.textContent).toContain(
      'The local transcription model could not process this recording.',
    );
    expect(fixture.nativeElement.textContent).toContain('Retry processing');
    expect(fixture.componentInstance.project()?.status).toBe('failed');
    expect(fixture.nativeElement.querySelector('.overall-gauge .ns-gauge--danger')).not.toBeNull();
  });

  it('requests cooperative cancellation and applies the persisted cancelled state', async () => {
    const cancellation = new Subject<ProcessingJob>();
    const cancelJob = vi.fn(() => cancellation.asObservable());
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(JOB),
            cancelJob,
            startProcessing: () => of(JOB),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    fixture.componentInstance.cancelProcessing();
    fixture.detectChanges();

    expect(cancelJob).toHaveBeenCalledWith(JOB.id);
    expect(fixture.componentInstance.actionPending()).toBe(true);
    expect(
      (fixture.nativeElement.querySelector('button.ns-button--danger') as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    cancellation.next({
      ...JOB,
      status: 'cancelled',
      currentStage: 'cancelled',
      progressPercent: 42,
      message: 'Processing was cancelled.',
      completedAt: '2026-07-15T10:02:00Z',
      version: 8,
    });
    cancellation.complete();
    fixture.detectChanges();

    expect(fixture.componentInstance.actionPending()).toBe(false);
    expect(fixture.componentInstance.project()?.status).toBe('uploaded');
    expect(fixture.nativeElement.textContent).toContain('The original video is kept');
    expect(fixture.nativeElement.textContent).toContain('Retry processing');
    expect(fixture.nativeElement.textContent).toContain('Not done');
  });

  it('enables cancellation as soon as a new job has been queued', async () => {
    const monitoring = new Subject<ProcessingJob>();
    const uploadedProject: Project = {
      ...PROJECT,
      status: 'uploaded',
      activeJobId: null,
    };
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(uploadedProject),
            startProcessing: () => of(JOB),
            monitorJob: () => monitoring.asObservable(),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();

    fixture.componentInstance.startProcessing();
    fixture.detectChanges();

    const cancelButton = fixture.nativeElement.querySelector(
      'button.ns-button--danger',
    ) as HTMLButtonElement;
    expect(fixture.componentInstance.actionPending()).toBe(false);
    expect(cancelButton.disabled).toBe(false);

    monitoring.complete();
  });

  it('offers Open review as the only primary action once processing is done', async () => {
    const completedJob: ProcessingJob = {
      ...JOB,
      status: 'completed',
      currentStage: 'completed',
      progressPercent: 100,
      message: 'Ready for review.',
      completedAt: '2026-07-15T10:05:00Z',
      version: 9,
    };
    await TestBed.configureTestingModule({
      imports: [ProcessingComponent],
      providers: [
        provideRouter([]),
        {
          provide: ApiService,
          useValue: {
            getProject: () => of(PROJECT),
            monitorJob: () => of(completedJob),
          },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(ProcessingComponent);
    fixture.componentRef.setInput('projectId', PROJECT.id);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const primary = Array.from(
      fixture.nativeElement.querySelectorAll('.ns-button--primary'),
    ) as HTMLAnchorElement[];
    expect(primary.length).toBe(1);
    expect(primary[0]?.textContent?.trim()).toBe('Open review');
    expect(primary[0]?.getAttribute('href')).toBe('/projects/project-1/review');
    expect(fixture.nativeElement.querySelector('.stage-list [role="progressbar"]')).toBeNull();
    expect(fixture.nativeElement.querySelector('ns-status-pill')?.textContent).toContain('Done');
  });
});
