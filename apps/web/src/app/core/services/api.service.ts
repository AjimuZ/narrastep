// Typed client for the local Narrastep API: projects, uploads, recordings, jobs, review data and packages.
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
  CompleteRecordingRequest,
  CreateExportRequest,
  CreateProjectRequest,
  CreateRecordingRequest,
  DocumentGeneration,
  ExportEstimate,
  GenerationModel,
  GenerationModelList,
  HealthResponse,
  MediaAsset,
  MergeSegmentsRequest,
  ProcessingJob,
  Project,
  ProjectExport,
  ProjectListResponse,
  ProjectStorage,
  Recording,
  RecordingChunkAck,
  RecordingTrackName,
  Screenshot,
  ScreenshotAssociation,
  SegmentScreenshot,
  SplitSegmentRequest,
  TranscriptSegment,
  StartProcessingRequest,
  StorageSummary,
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

  /** Loads the disk space each project uses, largest first, with the free space left. */
  getStorage(): Observable<StorageSummary> {
    return this.http.get<StorageSummary>(`${API_ROOT}/storage`);
  }

  /**
   * Deletes the original video (and the kept recording tracks) of a project that has a package of
   * its current review. Answers the project's new storage figures; the API refuses with
   * `PACKAGE_REQUIRED` before an export.
   */
  removeSourceVideo(projectId: string): Observable<ProjectStorage> {
    return this.http.delete<ProjectStorage>(`${API_ROOT}/projects/${projectId}/source-video`);
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

  /**
   * Opens a recording session and its `draft` project (named by `request.name` or by the API after
   * the date). Answers the session with both tracks empty and the capture settings to apply.
   */
  createRecording(request: CreateRecordingRequest): Observable<Recording> {
    return this.http.post<Recording>(`${API_ROOT}/recordings`, request);
  }

  /** Reads a session's state and what each track stores, including gaps; 404 when discarded. */
  getRecording(recordingId: string): Observable<Recording> {
    return this.http.get<Recording>(`${API_ROOT}/recordings/${recordingId}`);
  }

  /**
   * Stores one `MediaRecorder` chunk as the raw request body. `startOffsetMs` is the track's start
   * relative to the session's common start; the server keeps the first value per track. Repeating
   * a stored sequence with the same bytes answers `duplicate: true`; different bytes fail with
   * `RECORDING_CHUNK_CONFLICT`.
   */
  uploadRecordingChunk(
    recordingId: string,
    track: RecordingTrackName,
    sequence: number,
    chunk: Blob,
    startOffsetMs: number | null,
  ): Observable<RecordingChunkAck> {
    const params: Record<string, number> = {};
    if (startOffsetMs !== null) {
      params['startOffsetMs'] = startOffsetMs;
    }
    return this.http.put<RecordingChunkAck>(
      `${API_ROOT}/recordings/${recordingId}/tracks/${track}/chunks/${sequence}`,
      chunk,
      {
        params,
        headers: { 'Content-Type': chunk.type.split(';', 1)[0] || 'application/octet-stream' },
      },
    );
  }

  /**
   * Saves an open session as its project's source video; answers the project `uploaded` with no
   * job. Fails with `RECORDING_INCOMPLETE` while a chunk is missing unless `stopAtGap` is set.
   */
  completeRecording(recordingId: string, request: CompleteRecordingRequest): Observable<Project> {
    return this.http.post<Project>(`${API_ROOT}/recordings/${recordingId}/complete`, request);
  }

  /** Discards an open session together with its chunks and its project. */
  discardRecording(recordingId: string): Observable<void> {
    return this.http.delete<void>(`${API_ROOT}/recordings/${recordingId}`);
  }

  /** Builds a range-capable relative URL for the native video element. */
  videoUrl(projectId: string): string {
    return `${API_ROOT}/projects/${projectId}/video`;
  }

  /**
   * Queues the real local processing workflow for an uploaded project. `choices` carries the type
   * and language picked in the Process dialog; a project without a type is refused without them.
   */
  startProcessing(
    projectId: string,
    choices: StartProcessingRequest = {},
  ): Observable<ProcessingJob> {
    return this.http.post<ProcessingJob>(`${API_ROOT}/projects/${projectId}/process`, choices);
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

  /** Lists the local generation models and whether the kill switch allows them. */
  listModels(): Observable<GenerationModelList> {
    return this.http.get<GenerationModelList>(`${API_ROOT}/models`);
  }

  /** Reads one model's state and download progress. */
  getModel(modelId: string): Observable<GenerationModel> {
    return this.http.get<GenerationModel>(`${API_ROOT}/models/${modelId}`);
  }

  /** Starts, or resumes after a failure, the download; answers the entry, usually `downloading`. */
  downloadModel(modelId: string): Observable<GenerationModel> {
    return this.http.post<GenerationModel>(`${API_ROOT}/models/${modelId}/download`, {});
  }

  /** Cancels a download or its verification and deletes the partial files. */
  cancelModelDownload(modelId: string): Observable<GenerationModel> {
    return this.http.post<GenerationModel>(`${API_ROOT}/models/${modelId}/cancel`, {});
  }

  /** Enables an installed model; refused under the kill switch. */
  enableModel(modelId: string): Observable<GenerationModel> {
    return this.http.post<GenerationModel>(`${API_ROOT}/models/${modelId}/enable`, {});
  }

  /** Disables an enabled model and keeps its files. */
  disableModel(modelId: string): Observable<GenerationModel> {
    return this.http.post<GenerationModel>(`${API_ROOT}/models/${modelId}/disable`, {});
  }

  /** Deletes the files of an installed or enabled model; answers the `not_installed` entry. */
  deleteModel(modelId: string): Observable<GenerationModel> {
    return this.http.delete<GenerationModel>(`${API_ROOT}/models/${modelId}`);
  }

  /**
   * Polls a model every two seconds and completes after the first answer that is neither
   * `downloading` nor `verifying`, which it still emits. A failed poll is skipped, so an API
   * restart does not end the watch; unsubscribing stops the timer.
   */
  monitorModel(modelId: string): Observable<GenerationModel> {
    return timer(0, 2_000).pipe(
      switchMap(() => this.getModel(modelId).pipe(catchError(() => EMPTY))),
      takeWhile((model) => model.state === 'downloading' || model.state === 'verifying', true),
    );
  }

  /** Queues the local generation of a document; answers the `queued` generation (202). */
  createGeneration(projectId: string): Observable<DocumentGeneration> {
    return this.http.post<DocumentGeneration>(`${API_ROOT}/projects/${projectId}/generations`, {});
  }

  /** Lists the generations of a project, newest first. */
  listGenerations(projectId: string): Observable<DocumentGeneration[]> {
    return this.http
      .get<DocumentGeneration[] | { items: DocumentGeneration[] }>(
        `${API_ROOT}/projects/${projectId}/generations`,
      )
      .pipe(map(unwrapItems));
  }

  /** Reads one generation: status, progress per part and failure code. */
  getGeneration(generationId: string): Observable<DocumentGeneration> {
    return this.http.get<DocumentGeneration>(`${API_ROOT}/generations/${generationId}`);
  }

  /** Cancels a queued generation at once, or asks a running one to stop; answers its new state. */
  cancelGeneration(generationId: string): Observable<DocumentGeneration> {
    return this.http.post<DocumentGeneration>(`${API_ROOT}/generations/${generationId}/cancel`, {});
  }

  /** Reads the Markdown text of a completed generation. */
  getGenerationDocument(generationId: string): Observable<string> {
    return this.http.get(`${API_ROOT}/generations/${generationId}/document`, {
      responseType: 'text',
    });
  }

  /**
   * Polls a generation every two seconds and completes after the first answer that is final
   * (`completed`, `failed` or `cancelled`), which it still emits. A failed poll is skipped, so an
   * API restart does not end the watch; unsubscribing stops the timer.
   */
  monitorGeneration(generationId: string): Observable<DocumentGeneration> {
    return timer(0, 2_000).pipe(
      switchMap(() => this.getGeneration(generationId).pipe(catchError(() => EMPTY))),
      takeWhile(
        (generation) =>
          generation.status === 'queued' ||
          generation.status === 'processing' ||
          generation.status === 'cancelling',
        true,
      ),
    );
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
