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
import { EMPTY, catchError, finalize, forkJoin, of, switchMap, tap } from 'rxjs';

import { ExportEstimate, Project, ProjectExport } from '../../core/api.models';
import { NarrastepApiError } from '../../core/services/api-error.interceptor';
import { ApiService } from '../../core/services/api.service';

@Component({
  selector: 'ns-export',
  imports: [DatePipe, RouterLink],
  templateUrl: './export.component.html',
  styleUrl: './export.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExportComponent implements OnInit {
  readonly projectId = input.required<string>();
  readonly project = signal<Project | null>(null);
  readonly exports = signal<ProjectExport[]>([]);
  readonly estimate = signal<ExportEstimate | null>(null);
  readonly includeOriginalVideo = signal(false);
  readonly loading = signal(true);
  readonly creating = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly currentExport = computed(
    () =>
      this.exports().find((item) => item.status === 'queued' || item.status === 'processing') ??
      null,
  );
  readonly completedExports = computed(() =>
    this.exports().filter((item) => item.status === 'completed' || item.status === 'failed'),
  );

  readonly standardFiles = [
    'README.md',
    '01-ai-input/llm-input.md',
    '01-ai-input/generation-prompt.md',
    '02-screenshots/step-NNN_MMmSSs.png',
    '03-transcript/reviewed-transcript.md',
    '03-transcript/subtitles.srt',
    '03-transcript/raw-transcript.json',
    '04-chatgpt-batches/batch-NN-of-NN/batch-input.md + up to 19 images',
    'metadata/manifest.json (schema 0.3.0)',
    'metadata/source.json',
  ];

  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  /** Loads project context and resumes polling only when an export is still active. */
  ngOnInit(): void {
    forkJoin({
      project: this.api.getProject(this.projectId()),
      exports: this.api.listExports(this.projectId()),
      estimate: this.api
        .estimateExport(this.projectId(), this.includeOriginalVideo())
        .pipe(catchError(() => of(null))),
    })
      .pipe(
        tap(({ project, exports, estimate }) => {
          this.project.set(project);
          this.exports.set(sortExports(exports));
          this.estimate.set(estimate);
          // The initial persisted snapshot is renderable even when background polling remains active.
          this.loading.set(false);
        }),
        switchMap(({ exports }) => {
          return exports.some((item) => item.status === 'queued' || item.status === 'processing')
            ? this.api.monitorExports(this.projectId())
            : of(exports);
        }),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error, 'Narrastep could not load export history.'));
          return EMPTY;
        }),
      )
      .subscribe((exports) => this.exports.set(sortExports(exports)));
  }

  /** Reads the explicit source-video checkbox while preserving its safe false default. */
  setIncludeOriginalVideo(event: Event): void {
    this.includeOriginalVideo.set((event.target as HTMLInputElement).checked);
    this.refreshEstimate();
  }

  /** Refreshes the conservative estimate using the same backend selection policy as generation. */
  refreshEstimate(): void {
    this.api
      .estimateExport(this.projectId(), this.includeOriginalVideo())
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        catchError(() => of(null)),
      )
      .subscribe((estimate) => this.estimate.set(estimate));
  }

  /** Starts immutable ZIP generation and follows its persisted state until terminal. */
  createExport(): void {
    if (this.creating() || this.currentExport()) {
      return;
    }
    this.creating.set(true);
    this.errorMessage.set(null);
    this.api
      .createExport(this.projectId(), {
        includeOriginalVideo: this.includeOriginalVideo(),
      })
      .pipe(
        tap((created) => this.exports.update((exports) => sortExports([created, ...exports]))),
        switchMap(() => this.api.monitorExports(this.projectId())),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.creating.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(this.errorText(error, 'Narrastep could not generate this export.'));
          return EMPTY;
        }),
      )
      .subscribe((exports) => this.exports.set(sortExports(exports)));
  }

  /** Builds the local download endpoint for a completed immutable ZIP. */
  downloadUrl(exportId: string): string {
    return this.api.exportDownloadUrl(exportId);
  }

  /** Formats optional ZIP size metadata without implying a value before completion. */
  formatFileSize(bytes: number | null): string {
    if (bytes === null) {
      return 'Size unavailable';
    }
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  /** Reads one numeric immutable-export metric while supporting older export records. */
  exportMetric(item: ProjectExport, metric: string): number | null {
    const value = item.metrics?.[metric];
    return typeof value === 'number' ? value : null;
  }

  /** Uses backend-safe messages and suppresses unknown exception details. */
  private errorText(error: unknown, fallback: string): string {
    return error instanceof NarrastepApiError ? error.message : fallback;
  }
}

/** Orders export history deterministically with the newest record first. */
function sortExports(exports: ProjectExport[]): ProjectExport[] {
  return [...exports].sort(
    (left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime(),
  );
}
