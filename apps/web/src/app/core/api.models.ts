// Types of the local API payloads (camelCase JSON) used by the web client.
/** Project type (ADR 0009); the API keeps the historical field name `mode`. */
export type ProjectMode = 'task_briefing' | 'meeting' | 'tutorial' | 'general';

export type ProjectStatus =
  'draft' | 'uploaded' | 'queued' | 'processing' | 'review' | 'exporting' | 'completed' | 'failed';

export interface Project {
  id: string;
  name: string;
  description: string | null;
  mode: ProjectMode;
  sourceLanguage: string;
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
}

export type UpdateProjectRequest = Partial<CreateProjectRequest>;

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

export interface TranscriptSegment {
  id: string;
  projectId: string;
  segmentIndex: number;
  startSeconds: number;
  endSeconds: number;
  speakerLabel: string | null;
  rawText: string;
  reviewedText: string;
  confidence: number | null;
  words?: TranscriptWord[];
  createdAt: string;
  updatedAt: string;
}

export interface UpdateSegmentRequest {
  reviewedText: string;
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
