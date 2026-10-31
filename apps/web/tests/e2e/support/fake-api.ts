// Deterministic local API for the browser checks: one project per scenario, no backend needed.
import type { BrowserContext } from '@playwright/test';

const timestamp = '2026-07-15T10:00:00Z';
const MB = 1024 * 1024;
const projectName = 'Contract search and customer onboarding workflow for operations';

/** Project and job states a scenario puts the fake API in. */
export interface FakeScenario {
  /** Status of `project-1`; the project list also shows one project per other status. */
  status: 'processing' | 'review' | 'failed';
  /** State of the job `job-1` (used when the project is processing or failed). */
  jobStatus?: 'processing' | 'failed';
  /** Kill switch of local generation as `GET /models` reports it; `available` by default. */
  localGeneration?: 'available' | 'disabled';
  /**
   * How a started model download behaves: `stalled` (default) stays at 40% so a screen can be
   * checked in that state; `advancing` moves on at every poll until the model is installed.
   */
  modelDownload?: 'stalled' | 'advancing';
}

/** Install states of a generation model, as `GET /models` reports them. */
export type FakeModelState =
  'not_installed' | 'downloading' | 'verifying' | 'installed' | 'enabled' | 'failed';

/** Builds a generation model entry; `sizeGb` is the total download in GB (binary). */
function modelRow(id: string, label: string, state: FakeModelState, sizeGb: number, done = 0) {
  const totalBytes = Math.round(sizeGb * 1024 * MB);
  return {
    id,
    label,
    state,
    downloadedBytes: state === 'not_installed' ? 0 : Math.round(totalBytes * done),
    totalBytes,
    files: [{ name: `${id}.gguf`, sizeBytes: totalBytes }],
    repository: `ggml-org/${id}-GGUF`,
    revision: '6938c58e698502037401a5f19f6905f2fb2f7dee',
    licenseName: 'Apache License 2.0',
    licenseUrl: 'https://ai.google.dev/gemma/docs/gemma_4_license',
    errorMessage: null,
    updatedAt: null,
  };
}

/** Builds one project payload with the fields the screens read. */
function project(
  id: string,
  name: string,
  status: string,
  mode: string,
  activeJobId: string | null,
) {
  return {
    id,
    name,
    description: 'Search workflow',
    mode,
    sourceLanguage: 'en',
    glossary: [],
    detectedLanguage: 'en',
    status,
    createdAt: timestamp,
    updatedAt: timestamp,
    videoAssetId: status === 'draft' ? null : 'asset-1',
    activeJobId,
    videoAsset:
      status === 'draft'
        ? null
        : {
            id: 'asset-1',
            projectId: id,
            assetType: 'source_video',
            originalFilename: 'customer-onboarding-recording-with-detailed-workflow.mp4',
            mimeType: 'video/mp4',
            sizeBytes: 16_000_000,
            durationSeconds: 754,
            width: 1280,
            height: 720,
            createdAt: timestamp,
          },
    activeSegmentCount: status === 'review' || status === 'completed' ? 2 : 0,
    screenshotCount: status === 'review' || status === 'completed' ? 4 : 0,
  };
}

/** Builds a transcript segment, unlabelled unless a speaker label (P05.5) is given. */
function segment(
  id: string,
  index: number,
  start: number,
  end: number,
  text: string,
  speaker: { speakerLabel: 'you' | 'others' | null; speakerSource: 'automatic' | 'user' | null } = {
    speakerLabel: null,
    speakerSource: null,
  },
) {
  return {
    id,
    projectId: 'project-1',
    segmentIndex: index,
    startSeconds: start,
    endSeconds: end,
    ...speaker,
    rawText: text,
    reviewedText: text,
    confidence: 0.92,
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

/** Builds a processing job in the given state. */
function job(status: 'processing' | 'failed') {
  const failed = status === 'failed';
  return {
    id: 'job-1',
    projectId: 'project-1',
    status,
    currentStage: failed ? 'transcribing' : 'extracting_frames',
    progressPercent: failed ? 38 : 54,
    message: failed ? 'Transcription failed.' : 'Extracting frames.',
    errorCode: failed ? 'TRANSCRIPTION_FAILED' : null,
    errorMessage: failed ? 'The audio could not be transcribed.' : null,
    startedAt: timestamp,
    completedAt: null,
    createdAt: timestamp,
    version: 4,
  };
}

/** Name of the only project in the Storage list whose original video can be removed. */
export const REMOVABLE_PROJECT = 'Weekly operations meeting';

/** Builds a Storage row from its parts in MB; the total is their sum, as in the real API. */
function storageRow(
  projectId: string,
  name: string,
  status: string,
  removalBlockedReason: 'no_video' | 'already_removed' | 'busy' | 'package_required' | null,
  megabytes: { source?: number; audio?: number; screenshots?: number; exports?: number },
) {
  const categories = {
    source: (megabytes.source ?? 0) * MB,
    audio: (megabytes.audio ?? 0) * MB,
    transcript: 0,
    screenshots: (megabytes.screenshots ?? 0) * MB,
    exports: (megabytes.exports ?? 0) * MB,
    other: 0,
  };
  return {
    projectId,
    name,
    status,
    totalBytes: Object.values(categories).reduce((sum, bytes) => sum + bytes, 0),
    categories,
    sourceVideoBytes: categories.source,
    sourceRemovedAt: removalBlockedReason === 'already_removed' ? timestamp : null,
    removalBlockedReason,
  };
}

/** Registers the fake API on a browser context for one scenario; unknown routes answer 404. */
export async function useFakeApi(context: BrowserContext, scenario: FakeScenario): Promise<void> {
  // One row per removal state, so every Storage check covers the button, each reason and the link.
  let storageRows = [
    storageRow('project-2', REMOVABLE_PROJECT, 'completed', null, {
      source: 16,
      audio: 1,
      screenshots: 3,
      exports: 1,
    }),
    storageRow('project-1', projectName, 'review', 'package_required', {
      source: 12,
      screenshots: 2,
    }),
    storageRow('project-5', 'Escalation call', 'completed', 'already_removed', {
      screenshots: 2,
      exports: 1,
    }),
    storageRow('project-6', 'Queued tutorial', 'queued', 'busy', { source: 1 }),
    storageRow('project-3', 'New hire briefing', 'draft', 'no_video', {}),
  ];
  // Light is installed and Quality is not, so Settings shows both an Enable and an Install control.
  let models = [
    modelRow('gemma-4-e2b', 'Light', 'installed', 3.2, 1),
    modelRow('gemma-4-e4b', 'Quality', 'not_installed', 5.2),
  ];
  /** Returns the model with another state and the given share of the download received. */
  const withState = (model: (typeof models)[number], state: FakeModelState, done = 0) => ({
    ...model,
    state,
    downloadedBytes: state === 'not_installed' ? 0 : Math.round(model.totalBytes * done),
  });
  // The first segment carries an automatic speaker label, so every screen check also covers the
  // Review speaker select with its hint; the second is unlabelled.
  const segments = [
    segment('segment-1', 0, 0, 6, 'Select Search and review the results.', {
      speakerLabel: 'you',
      speakerSource: 'automatic',
    }),
    segment('segment-2', 1, 6, 12, 'Open the customer record from the list.'),
  ];
  const shots = [
    { id: 'shot-1', timestampSeconds: 2, role: 'primary', isUserSelected: false },
    { id: 'shot-2', timestampSeconds: 4, role: 'alternative', isUserSelected: false },
  ].map((shot) => ({
    ...shot,
    projectId: 'project-1',
    width: 1280,
    height: 720,
    qualityScore: 0.9,
    isCandidate: true,
    createdAt: timestamp,
  }));
  const exportRow = (status: 'completed' | 'failed', sequence: number) => ({
    id: `export-${sequence}`,
    projectId: 'project-1',
    status,
    includeOriginalVideo: false,
    progressPercent: status === 'completed' ? 100 : 40,
    currentStage: status === 'completed' ? 'completed' : 'writing_batches',
    errorCode: status === 'failed' ? 'EXPORT_FAILED' : null,
    errorMessage: status === 'failed' ? 'The package could not be written.' : null,
    sizeBytes: status === 'completed' ? 4096 : null,
    sequence,
    downloadName: `2026-07-15_tutorial_contract-search_export-0${sequence}.zip`,
    createdAt: timestamp,
    completedAt: status === 'completed' ? timestamp : null,
  });

  await context.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const current = project(
      'project-1',
      projectName,
      scenario.status,
      'tutorial',
      scenario.status === 'review' ? null : 'job-1',
    );

    if (path === '/api/v1/health') {
      await route.fulfill({
        json: { status: 'ok', version: '0.2.0', database: 'ok', redis: 'ok', worker: 'ok' },
      });
    } else if (path === '/api/v1/projects' && method === 'GET') {
      const items = [
        current,
        project('project-2', 'Weekly operations meeting', 'completed', 'meeting', null),
        project('project-3', 'New hire briefing', 'draft', 'task_briefing', null),
        project('project-4', 'Billing walkthrough', 'uploaded', 'general', null),
        project('project-5', 'Escalation call', 'failed', 'meeting', null),
        project('project-6', 'Queued tutorial', 'queued', 'tutorial', null),
      ];
      await route.fulfill({ json: { items, total: items.length } });
    } else if (path === '/api/v1/projects/project-1' && method === 'GET') {
      await route.fulfill({ json: current });
    } else if (path === '/api/v1/jobs/job-1') {
      await route.fulfill({ json: job(scenario.jobStatus ?? 'processing') });
    } else if (path === '/api/v1/projects/project-1/video') {
      await route.fulfill({ status: 206, contentType: 'video/mp4', body: '' });
    } else if (path === '/api/v1/projects/project-1/segments') {
      await route.fulfill({ json: segments });
    } else if (/^\/api\/v1\/segments\/[^/]+$/.test(path) && method === 'PATCH') {
      // A review save: applies the sent text and speaker label to the stored segment.
      const index = segments.findIndex((item) => path.endsWith(`/${item.id}`));
      const body = route.request().postDataJSON() as {
        reviewedText?: string;
        speakerLabel?: 'you' | 'others' | null;
      };
      const current = segments[index]!;
      segments[index] = {
        ...current,
        reviewedText: body.reviewedText ?? current.reviewedText,
        ...('speakerLabel' in body
          ? { speakerLabel: body.speakerLabel ?? null, speakerSource: 'user' as const }
          : {}),
      };
      await route.fulfill({ json: segments[index] });
    } else if (/\/api\/v1\/segments\/[^/]+\/screenshots$/.test(path)) {
      await route.fulfill({ json: shots });
    } else if (/\/api\/v1\/screenshots\/[^/]+\/(content|thumbnail)$/.test(path)) {
      await route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 9"><rect width="16" height="9" fill="#3c3e55"/></svg>',
      });
    } else if (path === '/api/v1/projects/project-1/export-estimate') {
      await route.fulfill({
        json: {
          canonicalImageCount: 4,
          batchCount: 1,
          batchImageCopyCount: 4,
          estimatedZipBytes: 8192,
          isApproximate: true,
          redundancyWarning: false,
        },
      });
    } else if (path === '/api/v1/projects/project-1/exports' && method === 'GET') {
      await route.fulfill({ json: [exportRow('failed', 2), exportRow('completed', 1)] });
    } else if (path === '/api/v1/storage' && method === 'GET') {
      await route.fulfill({
        json: {
          totalBytes: storageRows.reduce((sum, row) => sum + row.totalBytes, 0),
          freeBytes: 20 * 1024 * MB,
          items: storageRows,
        },
      });
    } else if (/^\/api\/v1\/projects\/[^/]+\/source-video$/.test(path) && method === 'DELETE') {
      // Like the API: only an allowed row loses its source files; any other is refused unchanged.
      const row = storageRows.find((item) => path.includes(`/${item.projectId}/`));
      if (row?.removalBlockedReason !== null) {
        await route.fulfill({
          status: 409,
          json: {
            error: { code: 'PACKAGE_REQUIRED', message: 'A package is required.', details: {} },
          },
        });
        return;
      }
      const updated = {
        ...row,
        totalBytes: row.totalBytes - row.categories.source,
        categories: { ...row.categories, source: 0 },
        sourceVideoBytes: 0,
        sourceRemovedAt: timestamp,
        removalBlockedReason: 'already_removed' as const,
      };
      storageRows = storageRows.map((item) => (item === row ? updated : item));
      await route.fulfill({ json: updated });
    } else if (path === '/api/v1/models' && method === 'GET') {
      await route.fulfill({
        json: { localGeneration: scenario.localGeneration ?? 'available', items: models },
      });
    } else if (/^\/api\/v1\/models\/[^/]+(\/[a-z]+)?$/.test(path)) {
      // Like the API: each action moves a model along its documented transitions and anything else
      // is a 409; the kill switch refuses a download and an enable.
      const [, modelId, action] = /^\/api\/v1\/models\/([^/]+)(?:\/([a-z]+))?$/.exec(path)!;
      const model = models.find((item) => item.id === modelId);
      const refuse = (status: number, code: string, message: string) =>
        route.fulfill({ status, json: { error: { code, message, details: {} } } });
      if (!model) {
        await refuse(404, 'MODEL_NOT_FOUND', 'The model does not exist.');
        return;
      }
      let next = model;
      if (method === 'GET') {
        // An advancing download moves on at every poll: 40% steps, then verifying, then installed.
        const done = model.downloadedBytes / model.totalBytes;
        if (scenario.modelDownload === 'advancing' && model.state === 'downloading') {
          next =
            done + 0.4 >= 1
              ? withState(model, 'verifying', 1)
              : withState(model, 'downloading', done + 0.4);
        } else if (scenario.modelDownload === 'advancing' && model.state === 'verifying') {
          next = withState(model, 'installed', 1);
        }
      } else if (
        scenario.localGeneration === 'disabled' &&
        (action === 'download' || action === 'enable')
      ) {
        await refuse(403, 'LOCAL_GENERATION_DISABLED', 'Local generation is turned off.');
        return;
      } else if (
        (action === 'download' && (model.state === 'not_installed' || model.state === 'failed')) ||
        (action === 'cancel' && (model.state === 'downloading' || model.state === 'verifying')) ||
        (action === 'enable' && model.state === 'installed') ||
        (action === 'disable' && model.state === 'enabled') ||
        (action === undefined &&
          method === 'DELETE' &&
          ['installed', 'enabled'].includes(model.state))
      ) {
        const states: Record<string, FakeModelState> = {
          download: 'downloading',
          cancel: 'not_installed',
          enable: 'enabled',
          disable: 'installed',
          delete: 'not_installed',
        };
        const state = states[action ?? 'delete']!;
        next = withState(model, state, state === 'downloading' ? 0.4 : 1);
      } else {
        await refuse(
          409,
          'MODEL_STATE_CONFLICT',
          'This action is not available in the model state.',
        );
        return;
      }
      models = models.map((item) => (item === model ? next : item));
      await route.fulfill({ json: next });
    } else {
      await route.fulfill({
        status: 404,
        json: { error: { code: 'NOT_FOUND', message: 'Not found.', details: {} } },
      });
    }
  });
}
