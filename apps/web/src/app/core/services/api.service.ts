// Typed client for the local Narrastep API: projects, uploads, jobs, review data and packages.
import {
  HttpClient,
  HttpEvent,
  HttpEventType,
  HttpRequest,
  HttpResponse,
  HttpUploadProgressEvent,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  EMPTY,
  Observable,
  Subscription,
  catchError,
  filter,
  map,
  shareReplay,
  switchMap,
  takeWhile,
  timer,
} from 'rxjs';

import {
  CreateExportRequest,
  CreateProjectRequest,
  ExportEstimate,
  HealthResponse,
  MediaAsset,
  MergeSegmentsRequest,
  ProcessingJob,
  Project,
  ProjectExport,
  ProjectListResponse,
  Screenshot,
  ScreenshotAssociation,
  SegmentScreenshot,
  SplitSegmentRequest,
  TranscriptSegment,
  UpdateProjectRequest,
  UpdateSegmentRequest,
  UploadProgress,
} from '../api.models';

const API_ROOT = '/api/v1';
const TERMINAL_JOB_STATES = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  /**
   * Reads the local stack health (API, database, Redis, worker) for the shell's status pill. The
   * request goes only to the same-origin API; HTTP or network errors reach the subscriber.
   */
  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>(`${API_ROOT}/health`);
  }

  /** Loads the project collection for the local dashboard. */
  listProjects(): Observable<ProjectListResponse> {
    return this.http.get<ProjectListResponse>(`${API_ROOT}/projects`);
  }

  /** Creates project metadata before a source video is uploaded. */
  createProject(request: CreateProjectRequest): Observable<Project> {
    return this.http.post<Project>(`${API_ROOT}/projects`, request);
  }

  /** Loads the persisted state needed by a project route after refresh. */
  getProject(projectId: string): Observable<Project> {
    return this.http.get<Project>(`${API_ROOT}/projects/${projectId}`);
  }

  /** Applies user-editable project metadata without changing generated evidence. */
  updateProject(projectId: string, request: UpdateProjectRequest): Observable<Project> {
    return this.http.patch<Project>(`${API_ROOT}/projects/${projectId}`, request);
  }

  /** Deletes project metadata and its dedicated local workspace. */
  deleteProject(projectId: string): Observable<void> {
    return this.http.delete<void>(`${API_ROOT}/projects/${projectId}`);
  }

  /** Streams a source video upload and reports browser-derived transfer progress. */
  uploadVideo(projectId: string, file: File): Observable<UploadProgress> {
    const body = new FormData();
    body.append('file', file, file.name);
    const request = new HttpRequest('POST', `${API_ROOT}/projects/${projectId}/video`, body, {
      reportProgress: true,
    });

    return this.http.request<MediaAsset>(request).pipe(
      filter(
        (
          event: HttpEvent<MediaAsset>,
        ): event is HttpUploadProgressEvent | HttpResponse<MediaAsset> =>
          event.type === HttpEventType.UploadProgress || event.type === HttpEventType.Response,
      ),
      map((event: HttpUploadProgressEvent | HttpResponse<MediaAsset>) => {
        if (event.type === HttpEventType.Response) {
          return { state: 'complete', percent: 100, asset: event.body } as UploadProgress;
        }

        const total = event.total ?? file.size;
        const percent = total > 0 ? Math.min(99, Math.round((event.loaded / total) * 100)) : 0;
        return { state: 'uploading', percent, asset: null } as UploadProgress;
      }),
    );
  }

  /** Builds a range-capable relative URL for the native video element. */
  videoUrl(projectId: string): string {
    return `${API_ROOT}/projects/${projectId}/video`;
  }

  /** Queues the real local processing workflow for an uploaded project. */
  startProcessing(projectId: string): Observable<ProcessingJob> {
    return this.http.post<ProcessingJob>(`${API_ROOT}/projects/${projectId}/process`, {});
  }

  /** Loads the latest persisted processing progress snapshot. */
  getJob(jobId: string): Observable<ProcessingJob> {
    return this.http.get<ProcessingJob>(`${API_ROOT}/jobs/${jobId}`);
  }

  /** Requests cooperative cancellation without claiming an already-running process stops instantly. */
  cancelJob(jobId: string): Observable<ProcessingJob> {
    return this.http.post<ProcessingJob>(`${API_ROOT}/jobs/${jobId}/cancel`, {});
  }

  /**
   * Watches job progress through SSE, starts two-second polling when the stream fails, and retries SSE.
   * The backend snapshot on every connection makes a browser refresh and reconnect safe.
   */
  monitorJob(jobId: string): Observable<ProcessingJob> {
    return new Observable<ProcessingJob>((subscriber) => {
      let eventSource: EventSource | null = null;
      let pollingSubscription: Subscription | null = null;
      let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
      let stopped = false;
      let lastVersion = -1;

      /** Emits only newer persisted versions and closes transport after a terminal state. */
      const emitJob = (job: ProcessingJob): void => {
        if (job.version >= lastVersion) {
          lastVersion = job.version;
          subscriber.next(job);
        }
        if (TERMINAL_JOB_STATES.has(job.status)) {
          stopped = true;
          eventSource?.close();
          pollingSubscription?.unsubscribe();
          if (reconnectTimer !== null) {
            clearTimeout(reconnectTimer);
          }
          subscriber.complete();
        }
      };

      /** Polls SQLite-backed job state while Redis or the browser event stream is unavailable. */
      const startPolling = (): void => {
        if (pollingSubscription || stopped) {
          return;
        }
        pollingSubscription = timer(0, 2_000)
          .pipe(
            switchMap(() =>
              this.getJob(jobId).pipe(
                // A transient API restart must not disable the next polling or SSE retry cycle.
                catchError(() => EMPTY),
              ),
            ),
          )
          .subscribe({ next: emitJob });
      };

      /** Parses documented progress and snapshot events, ignoring heartbeats and malformed payloads. */
      const handleEvent = (event: MessageEvent<string>): void => {
        const job = parseJobEvent(event.data);
        if (job) {
          emitJob(job);
        }
      };

      /** Opens the event stream and schedules a new attempt after a recoverable disconnect. */
      const connect = (): void => {
        if (stopped) {
          return;
        }
        eventSource?.close();
        eventSource = new EventSource(`${API_ROOT}/jobs/${jobId}/events`);
        eventSource.onopen = () => {
          pollingSubscription?.unsubscribe();
          pollingSubscription = null;
        };
        eventSource.onmessage = handleEvent;
        eventSource.addEventListener('snapshot', handleEvent as EventListener);
        eventSource.addEventListener('progress', handleEvent as EventListener);
        eventSource.addEventListener('completed', handleEvent as EventListener);
        eventSource.addEventListener('failed', handleEvent as EventListener);
        eventSource.onerror = () => {
          eventSource?.close();
          startPolling();
          reconnectTimer = setTimeout(connect, 10_000);
        };
      };

      connect();
      return () => {
        stopped = true;
        eventSource?.close();
        pollingSubscription?.unsubscribe();
        if (reconnectTimer !== null) {
          clearTimeout(reconnectTimer);
        }
      };
    }).pipe(shareReplay({ bufferSize: 1, refCount: true }));
  }

  /** Loads active transcript segments in their persisted review order. */
  listSegments(projectId: string): Observable<TranscriptSegment[]> {
    return this.http
      .get<TranscriptSegment[] | { items: TranscriptSegment[] }>(
        `${API_ROOT}/projects/${projectId}/segments`,
      )
      .pipe(map(unwrapItems));
  }

  /** Persists reviewed text while retaining the immutable raw transcript. */
  updateSegment(segmentId: string, request: UpdateSegmentRequest): Observable<TranscriptSegment> {
    return this.http.patch<TranscriptSegment>(`${API_ROOT}/segments/${segmentId}`, request);
  }

  /** Splits a segment at a user-reviewed text and timestamp boundary. */
  splitSegment(segmentId: string, request: SplitSegmentRequest): Observable<TranscriptSegment[]> {
    return this.http
      .post<TranscriptSegment[] | { items: TranscriptSegment[] }>(
        `${API_ROOT}/segments/${segmentId}/split`,
        request,
      )
      .pipe(map(unwrapItems));
  }

  /** Merges a contiguous ordered selection into one reviewed segment. */
  mergeSegments(request: MergeSegmentsRequest): Observable<TranscriptSegment> {
    return this.http.post<TranscriptSegment>(`${API_ROOT}/segments/merge`, request);
  }

  /** Loads the screenshot choices relevant to one selected transcript segment. */
  listSegmentScreenshots(segmentId: string): Observable<SegmentScreenshot[]> {
    return this.http
      .get<
        | SegmentScreenshot[]
        | ScreenshotAssociation[]
        | { items: SegmentScreenshot[] | ScreenshotAssociation[] }
      >(`${API_ROOT}/segments/${segmentId}/screenshots`)
      .pipe(
        map((response) => (Array.isArray(response) ? response : response.items)),
        map(normalizeSegmentScreenshots),
      );
  }

  /** Loads all generated screenshot records for project-level diagnostics. */
  listScreenshots(projectId: string): Observable<Screenshot[]> {
    return this.http
      .get<Screenshot[] | { items: Screenshot[] }>(`${API_ROOT}/projects/${projectId}/screenshots`)
      .pipe(map(unwrapItems));
  }

  /** Marks a candidate as the user-selected primary screenshot for a segment. */
  selectPrimaryScreenshot(
    segmentId: string,
    screenshotId: string,
  ): Observable<ScreenshotAssociation> {
    return this.http.put<ScreenshotAssociation>(
      `${API_ROOT}/segments/${segmentId}/primary-screenshot/${screenshotId}`,
      {},
    );
  }

  /** Removes the primary association while leaving generated evidence available. */
  removePrimaryScreenshot(segmentId: string): Observable<void> {
    return this.http.delete<void>(`${API_ROOT}/segments/${segmentId}/primary-screenshot`);
  }

  /** Builds a same-origin image URL so generated files are never fetched externally. */
  screenshotUrl(screenshotId: string): string {
    return `${API_ROOT}/screenshots/${screenshotId}/content`;
  }

  /** Builds the lightweight derivative URL used throughout normal review rendering. */
  screenshotThumbnailUrl(screenshotId: string): string {
    return `${API_ROOT}/screenshots/${screenshotId}/thumbnail`;
  }

  /** Recovers the exact current video frame and makes it explicit segment evidence. */
  recoverScreenshotFromVideo(
    segmentId: string,
    timestampSeconds: number,
  ): Observable<ScreenshotAssociation> {
    return this.http.post<ScreenshotAssociation>(
      `${API_ROOT}/segments/${segmentId}/screenshot-from-video`,
      { timestampSeconds },
    );
  }

  /** Calculates image, batch, copy, and conservative ZIP estimates without creating an export. */
  estimateExport(projectId: string, includeOriginalVideo: boolean): Observable<ExportEstimate> {
    return this.http.get<ExportEstimate>(`${API_ROOT}/projects/${projectId}/export-estimate`, {
      params: { includeOriginalVideo },
    });
  }

  /** Starts immutable ZIP generation with source video excluded unless explicitly selected. */
  createExport(projectId: string, request: CreateExportRequest): Observable<ProjectExport> {
    return this.http.post<ProjectExport>(`${API_ROOT}/projects/${projectId}/exports`, request);
  }

  /** Loads immutable export history newest first. */
  listExports(projectId: string): Observable<ProjectExport[]> {
    return this.http
      .get<ProjectExport[] | { items: ProjectExport[] }>(
        `${API_ROOT}/projects/${projectId}/exports`,
      )
      .pipe(map(unwrapItems));
  }

  /** Polls export history only while a queued or active export still needs UI updates. */
  monitorExports(projectId: string): Observable<ProjectExport[]> {
    return timer(0, 2_000).pipe(
      switchMap(() => this.listExports(projectId)),
      takeWhile(
        (exports) =>
          exports.some((item) => item.status === 'queued' || item.status === 'processing'),
        true,
      ),
    );
  }

  /** Returns the download route consumed by a normal browser anchor. */
  exportDownloadUrl(exportId: string): string {
    return `${API_ROOT}/exports/${exportId}/download`;
  }
}

/** Accepts both list and paginated-list responses during gradual API evolution. */
function unwrapItems<T>(response: T[] | { items: T[] }): T[] {
  return Array.isArray(response) ? response : response.items;
}

/** Converts association-rich responses into one stable view model for the screenshot panel. */
function normalizeSegmentScreenshots(
  values: SegmentScreenshot[] | ScreenshotAssociation[],
): SegmentScreenshot[] {
  return values.map((value) => {
    if ('screenshot' in value) {
      return {
        ...value.screenshot,
        associationId: value.id,
        role: value.role,
        isUserSelected: value.isUserSelected,
      };
    }
    return value;
  });
}

/** Validates the minimum persisted job fields before emitting an SSE payload. */
function parseJobEvent(data: string): ProcessingJob | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (!isRecord(parsed)) {
      return null;
    }
    const candidate = isRecord(parsed['job']) ? parsed['job'] : parsed;
    if (
      typeof candidate['id'] !== 'string' ||
      typeof candidate['status'] !== 'string' ||
      typeof candidate['progressPercent'] !== 'number'
    ) {
      return null;
    }
    return candidate as unknown as ProcessingJob;
  } catch {
    return null;
  }
}

/** Narrows parsed JSON before accessing API fields. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
