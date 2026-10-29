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
    const progress = fixture.nativeElement.querySelector('progress') as HTMLProgressElement;
    expect(content).toContain('Transcribing speech');
    expect(content).toContain('Transcribing locally.');
    expect(progress.value).toBe(42);
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
    expect(content).toContain('Retry from source');
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
    expect(fixture.nativeElement.textContent).toContain('Retry from source');
    expect(fixture.componentInstance.project()?.status).toBe('failed');
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
      (fixture.nativeElement.querySelector('button.button--danger') as HTMLButtonElement).disabled,
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
    expect(fixture.nativeElement.textContent).toContain('original upload is still available');
    expect(fixture.nativeElement.textContent).toContain('Retry from source');
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
      'button.button--danger',
    ) as HTMLButtonElement;
    expect(fixture.componentInstance.actionPending()).toBe(false);
    expect(cancelButton.disabled).toBe(false);

    monitoring.complete();
  });
});
