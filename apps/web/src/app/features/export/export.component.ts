// Export screen: package options, size estimate, creation progress and the package history.
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
import { errorText, localizedError } from '../../core/i18n/error-text';
import { formatFileSize } from '../../core/i18n/format';
import { ApiService } from '../../core/services/api.service';
import { StatusLabel, exportStatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';

/** Word for a stage the export worker persists; other stages fall back to a generic word. */
function exportStageLabel(stage: string | null): string {
  switch (stage) {
    case 'queued':
      return $localize`:@@export.stage.queued:Waiting to start`;
    case 'collecting_evidence':
      return $localize`:@@export.stage.collecting:Collecting the reviewed evidence`;
    case 'building_package':
      return $localize`:@@export.stage.building:Building the package`;
    default:
      return $localize`:@@export.stage.default:Creating the package`;
  }
}

/** Creates and lists the immutable packages of one project; `projectId` comes from the route. */
@Component({
  selector: 'ns-export',
  imports: [
    ActivityLineComponent,
    DatePipe,
    PixelIconComponent,
    RouterLink,
    StatusPillComponent,
    WindowComponent,
  ],
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

  /** Project name placeholder while the project loads. */
  readonly loadingProjectLabel = $localize`:@@common.loading_project:Loading project…`;

  readonly standardFiles = [
    'README.md',
    '01-ai-input/llm-input.md',
    '01-ai-input/generation-prompt.md',
    '02-screenshots/step-NNN_MMmSSs.png',
    '03-transcript/reviewed-transcript.md',
    '03-transcript/subtitles.srt',
    '03-transcript/raw-transcript.json',
    $localize`:@@export.file.batch:04-chatgpt-batches/batch-NN-of-NN/batch-input.md + up to 19 images`,
    $localize`:@@export.file.manifest:metadata/manifest.json (schema 0.3.0)`,
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
          this.errorMessage.set(
            this.errorText(
              error,
              $localize`:@@export.error_load:The package history could not be loaded. Reload the page to try again.`,
            ),
          );
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
          this.errorMessage.set(
            this.errorText(
              error,
              $localize`:@@export.error_create:The package could not be created. Try again.`,
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((exports) => this.exports.set(sortExports(exports)));
  }

  /** Builds the local download endpoint for a completed immutable ZIP. */
  downloadUrl(exportId: string): string {
    return this.api.exportDownloadUrl(exportId);
  }

  /** Formats optional ZIP size metadata; returns "Unknown" instead of implying a value. */
  formatFileSize(bytes: number | null): string {
    return bytes === null ? $localize`:@@common.unknown:Unknown` : formatFileSize(bytes);
  }

  /** Title of the history window with the number of packages. */
  historyTitle(): string {
    return $localize`:@@export.history_title:Packages (${this.completedExports().length}:COUNT:)`;
  }

  /** Word and pill tone for an export state. */
  statusLabel(item: ProjectExport): StatusLabel {
    return exportStatusLabel(item.status);
  }

  /** Readable word for the persisted stage of an active export; never a raw stage code. */
  stageLabel(item: ProjectExport): string {
    return exportStageLabel(item.currentStage);
  }

  /** Reads one numeric immutable-export metric while supporting older export records. */
  exportMetric(item: ProjectExport, metric: string): number | null {
    const value = item.metrics?.[metric];
    return typeof value === 'number' ? value : null;
  }

  /** A metric as text, or "Unknown" when an older export record has none. */
  metricText(item: ProjectExport, metric: string): string {
    const value = this.exportMetric(item, metric);
    return value === null ? $localize`:@@common.unknown:Unknown` : String(value);
  }

  /** Why a package failed, in the active language; see `localizedError`. */
  failureText(item: ProjectExport): string {
    return localizedError(
      item.errorCode ?? 'EXPORT_FAILED',
      item.errorMessage,
      $localize`:@@export.failed_default:This package was not created. Create a new package.`,
    );
  }

  /** Uses translated backend error codes and suppresses unknown exception details. */
  private errorText(error: unknown, fallback: string): string {
    return errorText(error, fallback);
  }
}

/** Orders export history deterministically with the newest record first. */
function sortExports(exports: ProjectExport[]): ProjectExport[] {
  return [...exports].sort(
    (left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime(),
  );
}
