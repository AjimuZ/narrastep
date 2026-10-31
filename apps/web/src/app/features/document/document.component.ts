// Document screen: generate, follow, cancel and read the local model's document beside the video.
import { DatePipe, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, Observable, Subscription, catchError, finalize, forkJoin } from 'rxjs';

import { DocumentGeneration, Project } from '../../core/api.models';
import { copyText } from '../../core/clipboard';
import { errorText, localizedError } from '../../core/i18n/error-text';
import { ApiService } from '../../core/services/api.service';
import { StatusLabel } from '../../shared/status-labels';
import {
  ActivityLineComponent,
  PixelIconComponent,
  SegmentedGaugeComponent,
  StatusPillComponent,
  WindowComponent,
} from '../../shared/ui';
import { parseDocument } from './document-markdown';

/** Whether a generation is still running or waiting, so it can be cancelled and polled. */
function isActive(generation: DocumentGeneration): boolean {
  return (
    generation.status === 'queued' ||
    generation.status === 'processing' ||
    generation.status === 'cancelling'
  );
}

/**
 * Generates a document for one project with the local model and shows the newest finished one with
 * the video beside it. Progress is the real `partsCompleted` of `partsTotal` from polling the API.
 * The route is reachable only while generation is available (`generationAvailableGuard`). Next to
 * the text it offers Copy as Markdown and a download of one Markdown file with the screenshots the
 * document cites embedded (P06.4d); including the document in a package is on the Export screen.
 */
@Component({
  selector: 'ns-document',
  imports: [
    ActivityLineComponent,
    DatePipe,
    NgTemplateOutlet,
    PixelIconComponent,
    RouterLink,
    SegmentedGaugeComponent,
    StatusPillComponent,
    WindowComponent,
  ],
  templateUrl: './document.component.html',
  styleUrl: './document.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DocumentComponent implements OnInit {
  readonly projectId = input.required<string>();
  readonly project = signal<Project | null>(null);
  /** The project's generations, newest first. */
  readonly generations = signal<DocumentGeneration[]>([]);
  readonly loading = signal(true);
  /** True while a start or cancel request is in flight, so a double click sends one request. */
  readonly busy = signal(false);
  readonly errorMessage = signal<string | null>(null);
  /** Text of the newest finished generation; null until it is read. */
  readonly documentText = signal<string | null>(null);
  readonly documentLoading = signal(false);
  readonly documentError = signal<string | null>(null);
  /** Outcome of the last Copy as Markdown, shown beside the button; null before any copy. */
  readonly copyState = signal<'copied' | 'failed' | null>(null);
  /** Result of the last action, announced politely to screen readers. */
  readonly notice = signal<string | null>(null);

  /** Project name placeholder while the project loads. */
  readonly loadingProjectLabel = $localize`:@@common.loading_project:Loading project…`;

  /** The generation that is queued, running or being cancelled; there is at most one. */
  readonly active = computed(() => this.generations().find(isActive) ?? null);
  /** The newest generation whose document can be read. */
  readonly finished = computed(
    () =>
      this.generations().find((item) => item.status === 'completed' && item.documentAvailable) ??
      null,
  );
  /** The newest generation, shown as a failure or cancellation while nothing is running. */
  readonly latest = computed(() => this.generations()[0] ?? null);
  readonly blocks = computed(() => parseDocument(this.documentText() ?? ''));
  /** True once the original video was removed after export (P09.2), so there is nothing to play. */
  readonly sourceRemoved = computed(() => !!this.project()?.videoAsset?.sourceRemovedAt);

  protected readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  /** One running poll per generation; every poll stops when the screen is left. */
  private readonly watches = new Map<string, Subscription>();
  /** Identifier of the generation whose document is shown or being read. */
  private documentOf: string | null = null;

  /** Loads the project and its generations, then resumes polling and reading what already exists. */
  ngOnInit(): void {
    this.destroyRef.onDestroy(() => this.watches.forEach((watch) => watch.unsubscribe()));
    forkJoin({
      project: this.api.getProject(this.projectId()),
      generations: this.api.listGenerations(this.projectId()),
    })
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.loading.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            errorText(
              error,
              $localize`:@@document.error_load:The document could not be loaded. Reload the page to try again.`,
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe(({ project, generations }) => {
        this.project.set(project);
        this.generations.set(generations);
        const active = this.active();
        if (active) {
          this.watch(active.id);
        }
        this.showFinished();
      });
  }

  /** Queues a new generation (the first one, or a regeneration) and follows its progress. */
  generate(): void {
    if (this.busy() || this.active()) {
      return;
    }
    this.request(
      this.api.createGeneration(this.projectId()),
      $localize`:@@document.notice.started:The document is being generated.`,
    );
  }

  /** Cancels the running generation; the worker confirms a running one within about a second. */
  cancel(): void {
    const active = this.active();
    if (this.busy() || !active || active.status === 'cancelling') {
      return;
    }
    this.request(
      this.api.cancelGeneration(active.id),
      $localize`:@@document.notice.cancel_requested:Cancelling the generation.`,
    );
  }

  /**
   * Puts the document text on the clipboard and says so next to the button. Does nothing until the
   * text was read. A browser or window that refuses the copy gets an error pointing to the download.
   */
  async copyMarkdown(): Promise<void> {
    const text = this.documentText();
    if (text === null) {
      return;
    }
    this.copyState.set(null);
    this.copyState.set((await copyText(text)) ? 'copied' : 'failed');
  }

  /** Reads the finished document again after a failed read. */
  retryDocument(): void {
    this.documentOf = null;
    this.showFinished();
  }

  /** Word and pill tone for a generation state. */
  statusLabel(generation: DocumentGeneration): StatusLabel {
    switch (generation.status) {
      case 'queued':
        return { label: $localize`:@@document.state.queued:Waiting to start`, tone: 'info' };
      case 'processing':
        return { label: $localize`:@@document.state.processing:Generating`, tone: 'wait' };
      case 'cancelling':
        return { label: $localize`:@@document.state.cancelling:Cancelling`, tone: 'wait' };
      case 'completed':
        return { label: $localize`:@@document.state.completed:Completed`, tone: 'ok' };
      case 'failed':
        return { label: $localize`:@@document.state.failed:Failed`, tone: 'error' };
      case 'cancelled':
        return { label: $localize`:@@document.state.cancelled:Cancelled`, tone: 'info' };
    }
  }

  /** Readable word for the persisted stage; never a raw stage code. */
  stageLabel(generation: DocumentGeneration): string {
    switch (generation.currentStage) {
      case 'queued':
        return $localize`:@@document.stage.queued:Waiting for the local worker`;
      case 'starting':
        return $localize`:@@document.stage.starting:Starting`;
      case 'preparing':
        return $localize`:@@document.stage.preparing:Collecting the reviewed evidence`;
      case 'starting_server':
        return $localize`:@@document.stage.starting_server:Starting the local model`;
      case 'generating':
        return $localize`:@@document.stage.generating:Writing the document`;
      case 'merging':
        return $localize`:@@document.stage.merging:Merging the parts`;
      case 'saving':
        return $localize`:@@document.stage.saving:Saving the document`;
      default:
        return $localize`:@@document.stage.default:Generating the document`;
    }
  }

  /** Parts written so far, for example “Parts written: 1 of 3”. */
  partsText(generation: DocumentGeneration): string {
    return $localize`:@@document.parts:Parts written: ${generation.partsCompleted}:DONE: of ${generation.partsTotal}:TOTAL:`;
  }

  /** Accessible name of the progress gauge, with the value. */
  gaugeLabel(generation: DocumentGeneration): string {
    return $localize`:@@document.gauge_label:Document generation, ${generation.progressPercent}:PERCENT:%`;
  }

  /** Why a generation failed, in the active language; see `localizedError`. */
  failureText(generation: DocumentGeneration): string {
    return localizedError(
      generation.errorCode ?? 'GENERATION_FAILED',
      generation.errorMessage,
      $localize`:@@document.failed_default:The document was not generated. Try again.`,
    );
  }

  /**
   * Runs one start or cancel request: disables the buttons meanwhile, replaces the generation with
   * the answer, announces what happened and follows it. A refusal is shown in an alert and the
   * list keeps its last known state.
   */
  private request(call: Observable<DocumentGeneration>, done: string): void {
    this.busy.set(true);
    this.errorMessage.set(null);
    this.notice.set(null);
    call
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.busy.set(false)),
        catchError((error: unknown) => {
          this.errorMessage.set(
            errorText(
              error,
              $localize`:@@document.error_request:Narrastep could not reach its local service. Check that it is running, then try again.`,
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((generation) => {
        this.upsert(generation);
        this.notice.set(done);
        if (isActive(generation)) {
          this.watch(generation.id);
        }
        this.focusStatus();
      });
  }

  /** Polls a generation until it is final, replacing it in the list and reading its document. */
  private watch(generationId: string): void {
    if (this.watches.has(generationId)) {
      return;
    }
    const watch = this.api
      .monitorGeneration(generationId)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.watches.delete(generationId)),
      )
      .subscribe((generation) => {
        this.upsert(generation);
        if (generation.status === 'completed') {
          this.notice.set($localize`:@@document.notice.completed:The document is ready.`);
          this.showFinished();
        } else if (generation.status === 'failed') {
          this.notice.set($localize`:@@document.notice.failed:The generation failed.`);
        } else if (generation.status === 'cancelled') {
          this.notice.set($localize`:@@document.notice.cancelled:The generation was cancelled.`);
        }
      });
    this.watches.set(generationId, watch);
  }

  /** Reads the text of the newest finished generation unless it is already shown. */
  private showFinished(): void {
    const finished = this.finished();
    if (!finished || finished.id === this.documentOf) {
      return;
    }
    this.documentOf = finished.id;
    this.documentLoading.set(true);
    this.documentError.set(null);
    this.api
      .getGenerationDocument(finished.id)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.documentLoading.set(false)),
        catchError((error: unknown) => {
          this.documentOf = null;
          this.documentError.set(
            errorText(
              error,
              $localize`:@@document.error_read:The document could not be read. Try again.`,
            ),
          );
          return EMPTY;
        }),
      )
      .subscribe((text) => {
        this.copyState.set(null);
        this.documentText.set(text);
      });
  }

  /** Puts a fresh answer in the list: replaces the entry with the same id, else adds it first. */
  private upsert(generation: DocumentGeneration): void {
    this.generations.update((current) =>
      current.some((item) => item.id === generation.id)
        ? current.map((item) => (item.id === generation.id ? generation : item))
        : [generation, ...current],
    );
  }

  /** Moves focus to the status heading after an action removed the button that had it. */
  private focusStatus(): void {
    afterNextRender(() => document.getElementById('generation-status')?.focus(), {
      injector: this.injector,
    });
  }
}
