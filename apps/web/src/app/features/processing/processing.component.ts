// Processing screen: one project's persisted job, its stage list, real progress, cancel and retry.
import { DatePipe } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, Subscription, catchError, finalize, interval, of, switchMap, tap } from 'rxjs';

import { ProcessingJob, ProcessingStage, Project } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';
import { projectTypeLabel } from '../../shared/project-types';
import { StatusLabel, jobStatusLabel, projectStatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  SegmentedGaugeComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';

/** Position of one timeline stage relative to the worker's persisted current stage. */
export type StageState = 'pending' | 'active' | 'completed';

@Component({
  selector: 'ns-processing',
  imports: [
    ActivityLineComponent,
    DatePipe,
    PixelIconComponent,
    RouterLink,
    SegmentedGaugeComponent,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './processing.component.html',
  styleUrl: './processing.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProcessingComponent implements OnInit {
  readonly projectId = input.required<string>();
  readonly project = signal<Project | null>(null);
  readonly job = signal<ProcessingJob | null>(null);
  readonly loading = signal(true);
  readonly actionPending = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly transportMessage = signal<string | null>(null);
  readonly now = signal(Date.now());
  readonly isTerminal = computed(() => {
    const status = this.job()?.status;
    return (
      status === 'completed' ||
      status === 'failed' ||
      status === 'cancelled' ||
      status === 'interrupted'
    );
  });
  /** True when the job ended without results (failed or interrupted), so retry is offered. */
  readonly hasFailed = computed(() => {
    const status = this.job()?.status;
    return status === 'failed' || status === 'interrupted';
  });
  readonly canCancel = computed(() => {
    const status = this.job()?.status;
    return status === 'queued' || status === 'processing' || status === 'cancelling';
  });
  readonly timelineStages = computed<ProcessingStage[]>(() => {
    const sharedStages: ProcessingStage[] = [
      'validating',
      'reading_metadata',
      'extracting_audio',
      'transcribing',
    ];
    const selectionStages: ProcessingStage[] =
      this.job()?.selectionStrategy === 'adaptive_v1'
        ? ['scanning_frames', 'global_clustering', 'coverage_selection', 'thumbnail_persistence']
        : ['extracting_frames', 'deduplicating_frames'];
    return [
      ...sharedStages,
      ...selectionStages,
      'associating_frames',
      'saving_results',
      'completed',
    ];
  });

  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  private monitorSubscription: Subscription | null = null;

  /** Initializes refresh-safe project state and the persisted active job monitor. */
  ngOnInit(): void {
    interval(1_000)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.now.set(Date.now()));
    this.loadProject();
  }

  /** Reloads project state and resumes SSE or polling for an active persisted job. */
  loadProject(): void {
    this.monitorSubscription?.unsubscribe();
    this.loading.set(true);
    this.errorMessage.set(null);
    this.transportMessage.set(null);
    this.monitorSubscription = this.api
      .getProject(this.projectId())
      .pipe(
        tap((project) => this.project.set(project)),
        switchMap((project) =>
          project.activeJobId ? this.api.monitorJob(project.activeJobId) : of(null),
        ),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            this.errorText(
              error,
              'The project could not be loaded. Check that Narrastep is running, then try again.',
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((job) => {
        if (job) {
          this.applyJob(job);
        }
      });
  }

  /** Starts or retries real processing and switches directly into resilient job monitoring. */
  startProcessing(): void {
    if (this.actionPending()) {
      return;
    }
    this.monitorSubscription?.unsubscribe();
    this.actionPending.set(true);
    this.errorMessage.set(null);
    this.monitorSubscription = this.api
      .startProcessing(this.projectId())
      .pipe(
        tap((job) => {
          this.applyJob(job);
          // Enqueueing is complete; monitoring must not disable cooperative cancellation.
          this.actionPending.set(false);
        }),
        switchMap((job) => this.api.monitorJob(job.id)),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.actionPending.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            this.errorText(
              error,
              'Processing could not be started or followed. Try again to reload the project.',
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((job) => this.applyJob(job));
  }

  /** Requests cooperative cancellation and keeps monitoring until the worker confirms its result. */
  cancelProcessing(): void {
    const job = this.job();
    if (!job || !this.canCancel() || this.actionPending()) {
      return;
    }
    this.actionPending.set(true);
    this.errorMessage.set(null);
    this.api
      .cancelJob(job.id)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.actionPending.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            this.errorText(error, 'Processing could not be cancelled. Try again in a moment.'),
          );
          return EMPTY;
        }),
      )
      .subscribe((updatedJob) => this.applyJob(updatedJob));
  }

  /** Maps backend stage identifiers to concise operational language. */
  stageLabel(stage: ProcessingStage): string {
    const labels: Record<ProcessingStage, string> = {
      queued: 'Waiting for the worker',
      validating: 'Checking the video',
      reading_metadata: 'Reading video details',
      extracting_audio: 'Extracting audio',
      transcribing: 'Transcribing speech',
      extracting_frames: 'Finding screenshot candidates',
      scanning_frames: 'Scanning screen changes',
      global_clustering: 'Grouping similar screens',
      coverage_selection: 'Choosing screenshots',
      thumbnail_persistence: 'Saving screenshot previews',
      deduplicating_frames: 'Removing duplicate screenshots',
      associating_frames: 'Linking screenshots to segments',
      saving_results: 'Saving results',
      completed: 'Ready for review',
      failed: 'Processing failed',
      cancelled: 'Processing cancelled',
    };
    return labels[stage];
  }

  /** Marks a stage relative to the current persisted worker position for the visual timeline. */
  stageState(stage: ProcessingStage): StageState {
    const job = this.job();
    if (!job) {
      return 'pending';
    }
    if (job.status === 'completed') {
      return 'completed';
    }
    const currentIndex = this.timelineStages().indexOf(job.currentStage);
    const stageIndex = this.timelineStages().indexOf(stage);
    if (stage === job.currentStage) {
      return 'active';
    }
    return currentIndex >= 0 && stageIndex < currentIndex ? 'completed' : 'pending';
  }

  /**
   * State word for a stage row. Stages after the worker's position read "Waiting" while the job
   * runs and "Not done" once it stopped without results; the job does not report where it stopped.
   */
  stageStateLabel(stage: ProcessingStage): string {
    const state = this.stageState(stage);
    if (state === 'completed') {
      return 'Done';
    }
    if (state === 'active') {
      return this.isTerminal() ? 'Stopped' : 'Running';
    }
    return this.isTerminal() ? 'Not done' : 'Waiting';
  }

  /**
   * Real percentage for a stage gauge, or null when none exists. Only finished stages have one
   * (100); the API reports progress for the whole job, never for the running stage.
   */
  stagePercent(stage: ProcessingStage): number | null {
    return stage !== 'completed' && this.stageState(stage) === 'completed' ? 100 : null;
  }

  /** Word and tone of the project state for the status pill. */
  projectStatus(project: Project): StatusLabel {
    return projectStatusLabel(project.status);
  }

  /** Word and tone of the job state for the status pill. */
  jobStatus(job: ProcessingJob): StatusLabel {
    return jobStatusLabel(job.status);
  }

  /** Label of the project type. */
  typeLabel(project: Project): string {
    return projectTypeLabel(project.mode);
  }

  /** Video length as m:ss, or "Unknown" when the API has no duration. */
  durationLabel(project: Project): string {
    const durationSeconds = project.videoAsset?.durationSeconds;
    if (durationSeconds === null || durationSeconds === undefined) {
      return 'Unknown';
    }
    const minutes = Math.floor(durationSeconds / 60);
    const seconds = Math.floor(durationSeconds % 60);
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
  }

  /** Detected spoken-language code, else the chosen one; "auto" before detection reads "Automatic". */
  languageLabel(project: Project): string {
    const code = project.detectedLanguage ?? project.sourceLanguage;
    return code === 'auto' ? 'Automatic' : code;
  }

  /** Reads one safe numeric worker metric without assuming a particular diagnostics version. */
  metricValue(job: ProcessingJob, metric: string): number | null {
    const value = job.metrics?.[metric];
    return typeof value === 'number' ? value : null;
  }

  /** Calculates elapsed wall time from persisted worker timestamps, never from simulated progress. */
  elapsedLabel(job: ProcessingJob): string {
    const start = job.startedAt ?? job.createdAt;
    const end = job.completedAt ? new Date(job.completedAt).getTime() : this.now();
    const seconds = Math.max(0, Math.floor((end - new Date(start).getTime()) / 1000));
    const minutes = Math.floor(seconds / 60);
    return minutes > 0 ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
  }

  /** Video file name; "Unknown" when the response omits media details, "No video" before upload. */
  videoFilename(project: Project): string {
    if (project.videoAsset?.originalFilename) {
      return project.videoAsset.originalFilename;
    }
    return project.videoAssetId ? 'Unknown' : 'No video';
  }

  /** Applies one monotonic worker snapshot and mirrors terminal lifecycle state in the route view. */
  private applyJob(job: ProcessingJob): void {
    const previous = this.job();
    if (previous && job.version < previous.version) {
      return;
    }
    this.job.set(job);
    this.loading.set(false);
    this.project.update((project) => {
      if (!project) {
        return project;
      }
      if (job.status === 'completed') {
        return { ...project, status: 'review' };
      }
      if (job.status === 'failed' || job.status === 'interrupted') {
        return { ...project, status: 'failed' };
      }
      if (job.status === 'cancelled') {
        return { ...project, status: 'uploaded' };
      }
      return { ...project, status: job.status === 'queued' ? 'queued' : 'processing' };
    });
  }

  /** Prefers a structured local API error without exposing stack traces. */
  private errorText(error: unknown, fallback: string): string {
    return error instanceof NarrastepApiError ? error.message : fallback;
  }
}
