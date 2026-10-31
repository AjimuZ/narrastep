// Types of the local API payloads (camelCase JSON) used by the web client.
/** Project type (ADR 0009); the API keeps the historical field name `mode`. */
export type ProjectMode = 'task_briefing' | 'meeting' | 'tutorial' | 'general';

export type ProjectStatus =
  'draft' | 'uploaded' | 'queued' | 'processing' | 'review' | 'exporting' | 'completed' | 'failed';

export interface Project {
  id: string;
  name: string;
  description: string | null;
  /** Project type; null until the user chooses one, at the latest when processing starts (P05.4). */
  mode: ProjectMode | null;
  sourceLanguage: string;
  /** Names and terms passed to the transcriber as hints, in priority order (P09.1). */
  glossary: string[];
  detectedLanguage: string | null;
  status: ProjectStatus;
  createdAt: string;
  updatedAt: string;
  videoAssetId: string | null;
  activeJobId: string | null;
  videoAsset?: MediaAsset | null;
  activeSegmentCount?: number;
  screenshotCount?: number;
  /** Present for projects made with the recorder (P05.1); null for uploaded videos. */
  recording?: ProjectRecording | null;
}

/**
 * Recording session of a project made with the recorder. `interrupted` is true while it is still
 * `recording` but no chunk arrived for the configured time (tab closed or network down).
 */
export interface ProjectRecording {
  id: string;
  state: 'recording' | 'completed';
  interrupted: boolean;
}

/** `GET /health`: `status` is `ok` when every part answers and `degraded` otherwise. */
export interface HealthResponse {
  status: 'ok' | 'degraded';
  version: string;
  database: string;
  redis: string;
  worker: string;
}

export interface ProjectListResponse {
  items: Project[];
  total: number;
}

export interface CreateProjectRequest {
  name: string;
  description: string | null;
  mode: ProjectMode;
  sourceLanguage: string;
  /** Terms for the transcriber; the API normalizes them and stores an empty list when omitted. */
  glossary?: string[];
}

export type UpdateProjectRequest = Partial<CreateProjectRequest>;

/**
 * Choices sent with `POST /projects/{id}/process`. `mode` is required by the API when the project
 * has no type; both values are stored together with the queued state.
 */
export interface StartProcessingRequest {
  mode?: ProjectMode;
  sourceLanguage?: string;
  /** Replaces the project's glossary (an empty list clears it). */
  glossary?: string[];
}

export interface MediaAsset {
  id: string;
  projectId: string;
  assetType: string;
  originalFilename: string;
  mimeType: string;
  sizeBytes: number;
  durationSeconds: number | null;
  width: number | null;
  height: number | null;
  createdAt: string;
  /** Set once the original video was removed after export (P09.2); the file is gone. */
  sourceRemovedAt?: string | null;
}

/** Bytes a project uses, by workspace folder; the parts add up to the project total (P09.2). */
export interface StorageCategories {
  source: number;
  audio: number;
  transcript: number;
  screenshots: number;
  exports: number;
  other: number;
}

/** Why the original video cannot be removed now; null in `removalBlockedReason` when it can. */
export type RemovalBlockedReason = 'no_video' | 'already_removed' | 'busy' | 'package_required';

/** Disk use of one project and the state of its original video. */
export interface ProjectStorage {
  projectId: string;
  name: string;
  status: ProjectStatus;
  totalBytes: number;
  categories: StorageCategories;
  sourceVideoBytes: number;
  sourceRemovedAt: string | null;
  removalBlockedReason: RemovalBlockedReason | null;
}

/** Space used by every project, largest first, and the free space of the data volume. */
export interface StorageSummary {
  totalBytes: number;
  freeBytes: number | null;
  items: ProjectStorage[];
}

export type ProcessingJobStatus =
  'queued' | 'processing' | 'completed' | 'failed' | 'cancelling' | 'cancelled' | 'interrupted';

export type ProcessingStage =
  | 'queued'
  | 'validating'
  | 'reading_metadata'
  | 'extracting_audio'
  | 'transcribing'
  | 'extracting_frames'
  | 'scanning_frames'
  | 'global_clustering'
  | 'coverage_selection'
  | 'thumbnail_persistence'
  | 'deduplicating_frames'
  | 'associating_frames'
  | 'saving_results'
  | 'completed'
  | 'failed'
  | 'cancelled';

export interface ProcessingJob {
  id: string;
  projectId: string;
  status: ProcessingJobStatus;
  currentStage: ProcessingStage;
  progressPercent: number;
  message: string;
  errorCode: string | null;
  errorMessage: string | null;
  startedAt: string | null;
  completedAt: string | null;
  createdAt: string;
  version: number;
  selectionStrategy?: 'legacy' | 'adaptive_v1';
  ocrMode?: 'off' | 'selective';
  imageBudgetProfile?: 'compact' | 'balanced' | 'detailed';
  metrics?: Record<string, number | string | Record<string, number>>;
}

export interface TranscriptWord {
  startSeconds: number;
  endSeconds: number;
  text: string;
  probability?: number | null;
}

/** Who speaks in a segment of an in-app recording: the microphone user or the call (P05.5). */
export type SpeakerLabel = 'you' | 'others';

/** Who set a speaker label: the loudness comparison during processing, or the user in Review. */
export type SpeakerSource = 'automatic' | 'user';

export interface TranscriptSegment {
  id: string;
  projectId: string;
  segmentIndex: number;
  startSeconds: number;
  endSeconds: number;
  speakerLabel: SpeakerLabel | null;
  /** Null when no label was ever set; `user` also after the user cleared a label. */
  speakerSource: SpeakerSource | null;
  rawText: string;
  reviewedText: string;
  confidence: number | null;
  words?: TranscriptWord[];
  createdAt: string;
  updatedAt: string;
}

/** A review save: the text, the speaker label (null clears it) or both; at least one field. */
export interface UpdateSegmentRequest {
  reviewedText?: string;
  speakerLabel?: SpeakerLabel | null;
}

export interface SplitSegmentRequest {
  leftReviewedText: string;
  rightReviewedText: string;
  splitSeconds: number;
}

export interface MergeSegmentsRequest {
  segmentIds: string[];
}

export type ScreenshotRole = 'primary' | 'alternative' | 'before' | 'after';

export interface Screenshot {
  id: string;
  projectId: string;
  timestampSeconds: number;
  width: number;
  height: number;
  qualityScore: number;
  isCandidate: boolean;
  role?: ScreenshotRole;
  confidence?: number | null;
  isUserSelected?: boolean;
  selectionReason?: string;
  visualChangeScore?: number | null;
  textChangeScore?: number | null;
  clusterId?: string | null;
  isMandatory?: boolean;
  materializationState?: 'thumbnail_only' | 'png_ready';
  sharedSegmentCount?: number;
  createdAt: string;
}

export interface ScreenshotAssociation {
  id: string;
  segmentId: string;
  screenshotId: string;
  role: ScreenshotRole;
  confidence: number | null;
  coverageScore?: number | null;
  roleRankReason?: string | null;
  isUserSelected: boolean;
  screenshot: Screenshot;
  createdAt: string;
}

export type SegmentScreenshot = Screenshot & {
  associationId?: string;
  role: ScreenshotRole;
  isUserSelected: boolean;
};

export type ProjectExportStatus = 'queued' | 'processing' | 'completed' | 'failed';

export interface ProjectExport {
  id: string;
  projectId: string;
  status: ProjectExportStatus;
  includeOriginalVideo: boolean;
  progressPercent: number;
  currentStage: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  sizeBytes: number | null;
  /** 1-based export number within the project; null only for rows that were never numbered. */
  sequence: number | null;
  /** Name the ZIP downloads under; older exports report `narrastep-{id}.zip`. */
  downloadName: string;
  createdAt: string;
  completedAt: string | null;
  metrics?: Record<string, number | string | Record<string, number>>;
}

export interface ExportEstimate {
  canonicalImageCount: number;
  batchCount: number;
  batchImageCopyCount: number;
  estimatedZipBytes: number;
  isApproximate: boolean;
  redundancyWarning: boolean;
}

export interface CreateExportRequest {
  includeOriginalVideo: boolean;
}

export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface UploadProgress {
  state: 'uploading' | 'complete';
  percent: number;
  asset: MediaAsset | null;
}

/** One of the two tracks a recorder uploads: screen video with system audio, or microphone audio. */
export type RecordingTrackName = 'screen' | 'microphone';

/**
 * What the server stores for one track. `nextSequence` is one past the highest stored sequence;
 * `missingSequences` lists at most 100 gaps below it, `missingCount` all of them.
 */
export interface RecordingTrackSummary {
  track: RecordingTrackName;
  chunkCount: number;
  storedBytes: number;
  nextSequence: number;
  missingSequences: number[];
  missingCount: number;
  startOffsetMs: number | null;
}

/** Capture settings the recorder applies (`NARRASTEP_RECORDING_*` on the server). */
export interface RecordingCapture {
  chunkSeconds: number;
  videoBitsPerSecond: number;
  maxFrameRate: number;
}

/** `POST /recordings` and `GET /recordings/{id}`: one recording session and its project. */
export interface Recording {
  id: string;
  projectId: string;
  state: 'recording' | 'completed';
  interrupted: boolean;
  storedBytes: number;
  maxBytes: number;
  createdAt: string;
  updatedAt: string;
  lastChunkAt: string | null;
  tracks: RecordingTrackSummary[];
  capture: RecordingCapture;
}

/** Optional body of `POST /recordings`. */
export interface CreateRecordingRequest {
  name?: string;
  sourceLanguage?: string;
}

/** Optional body of `POST /recordings/{id}/complete`; `stopAtGap` saves up to the first gap. */
export interface CompleteRecordingRequest {
  name?: string;
  stopAtGap?: boolean;
}

/** Acknowledgement of one stored chunk; `duplicate` is true when the same bytes were already stored. */
export interface RecordingChunkAck {
  track: RecordingTrackName;
  sequence: number;
  sizeBytes: number;
  duplicate: boolean;
}

/** Install state of a local generation model; `downloading` and `verifying` are transient. */
export type ModelInstallState =
  'not_installed' | 'downloading' | 'verifying' | 'installed' | 'enabled' | 'failed';

/** One pinned file of a generation model; the path is never exposed. */
export interface GenerationModelFile {
  name: string;
  sizeBytes: number;
}

/**
 * `GET /models/{id}`: state, download progress and license of one generation model. `errorMessage`
 * is the readable English reason of a `failed` state and null otherwise.
 */
export interface GenerationModel {
  id: string;
  label: string;
  state: ModelInstallState;
  downloadedBytes: number;
  totalBytes: number;
  files: GenerationModelFile[];
  repository: string;
  revision: string;
  licenseName: string;
  licenseUrl: string;
  errorMessage: string | null;
  updatedAt: string | null;
}

/** `GET /models`: the catalog and whether the `NARRASTEP_LOCAL_GENERATION` kill switch is set. */
export interface GenerationModelList {
  localGeneration: 'available' | 'disabled';
  items: GenerationModel[];
}
