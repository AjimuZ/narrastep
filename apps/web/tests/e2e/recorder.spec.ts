// Recorder journey with Chromium's fake screen, system audio and microphone (docs/RECORDING.md, Verification):
// checks, level gauge, chunk uploads, pause, stop, save with a name and no processing; interrupted recordings; layout.
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { scanContrast } from './support/contrast';
import { useFakeApi } from './support/fake-api';

// Chromium answers screen and microphone requests itself with generated media: a test pattern
// with a beeping audio track for the screen, and a beeping fake microphone.
test.use({
  launchOptions: {
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--auto-select-desktop-capture-source=Entire screen',
    ],
  },
  permissions: ['microphone'],
});

/** The WebM (EBML) magic number every first chunk of a track must start with. */
const EBML_HEADER = [0x1a, 0x45, 0xdf, 0xa3];

interface StoredChunk {
  track: string;
  sequence: number;
  size: number;
  startOffsetMs: number | null;
  head: number[];
}

/** In-memory recording API on top of the shared fake API; records every request it receives. */
interface RecordingApi {
  chunks: StoredChunk[];
  created: unknown[];
  completed: unknown[];
  discarded: string[];
  processRequests: string[];
  saved: boolean;
}

const RECORDING_ID = 'rec-1';
const PROJECT_ID = 'project-rec';

/**
 * Installs the fake API plus recording routes. Chunks are kept as metadata (size, offset and first
 * bytes). After `complete` the project list contains the saved recording as `uploaded`. With
 * `interrupted` the project list starts with an open, interrupted recording instead.
 */
async function useRecordingApi(
  context: BrowserContext,
  options: { interrupted?: boolean } = {},
): Promise<RecordingApi> {
  await useFakeApi(context, { status: 'review' });
  const api: RecordingApi = {
    chunks: [],
    created: [],
    completed: [],
    discarded: [],
    processRequests: [],
    saved: false,
  };
  const timestamp = '2026-10-29T13:05:00Z';
  let projectName = options.interrupted ? 'Recording 2026-10-29 13:05' : '';
  const project = () => ({
    id: PROJECT_ID,
    name: projectName,
    description: null,
    mode: 'general',
    sourceLanguage: 'auto',
    detectedLanguage: null,
    status: api.saved ? 'uploaded' : 'draft',
    createdAt: timestamp,
    updatedAt: timestamp,
    videoAssetId: api.saved ? 'asset-rec' : null,
    activeJobId: null,
    videoAsset: api.saved
      ? {
          id: 'asset-rec',
          projectId: PROJECT_ID,
          assetType: 'source_video',
          originalFilename: 'source.webm',
          mimeType: 'video/webm',
          sizeBytes: 100_000,
          durationSeconds: 6,
          width: 1280,
          height: 720,
          createdAt: timestamp,
        }
      : null,
    recording: {
      id: RECORDING_ID,
      state: api.saved ? 'completed' : 'recording',
      interrupted: !api.saved && Boolean(options.interrupted),
    },
  });
  const session = () => ({
    id: RECORDING_ID,
    projectId: PROJECT_ID,
    state: api.saved ? 'completed' : 'recording',
    interrupted: Boolean(options.interrupted) && !api.saved,
    storedBytes: options.interrupted ? 3_276_800 : api.chunks.reduce((sum, c) => sum + c.size, 0),
    maxBytes: 4_294_967_296,
    createdAt: timestamp,
    updatedAt: timestamp,
    lastChunkAt: null,
    tracks: [],
    // One-second chunks keep the browser test short; the product default is five.
    capture: { chunkSeconds: 1, videoBitsPerSecond: 1_000_000, maxFrameRate: 15 },
  });

  await context.route('**/api/v1/projects**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/process')) {
      api.processRequests.push(url.pathname);
      await route.fulfill({
        status: 500,
        json: { error: { code: 'X', message: 'x', details: {} } },
      });
    } else if (url.pathname === '/api/v1/projects' && route.request().method() === 'GET') {
      const items = [project()];
      await route.fulfill({ json: { items, total: items.length } });
    } else if (url.pathname === `/api/v1/projects/${PROJECT_ID}`) {
      await route.fulfill({ json: project() });
    } else {
      await route.fallback();
    }
  });

  await context.route('**/api/v1/recordings**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const chunkMatch =
      /^\/api\/v1\/recordings\/rec-1\/tracks\/(screen|microphone)\/chunks\/(\d+)$/.exec(path);
    if (path === '/api/v1/recordings' && method === 'POST') {
      const body = request.postDataJSON() as { name?: string };
      api.created.push(body);
      projectName = body.name ?? 'Recording';
      await route.fulfill({ status: 201, json: session() });
    } else if (chunkMatch && method === 'PUT') {
      const bytes = request.postDataBuffer() ?? Buffer.alloc(0);
      const offset = url.searchParams.get('startOffsetMs');
      api.chunks.push({
        track: chunkMatch[1]!,
        sequence: Number(chunkMatch[2]),
        size: bytes.length,
        startOffsetMs: offset === null ? null : Number(offset),
        head: [...bytes.subarray(0, 4)],
      });
      await route.fulfill({
        status: 201,
        json: {
          track: chunkMatch[1],
          sequence: Number(chunkMatch[2]),
          sizeBytes: bytes.length,
          duplicate: false,
        },
      });
    } else if (path === `/api/v1/recordings/${RECORDING_ID}` && method === 'GET') {
      await route.fulfill({ json: session() });
    } else if (path === `/api/v1/recordings/${RECORDING_ID}` && method === 'DELETE') {
      api.discarded.push(RECORDING_ID);
      await route.fulfill({ status: 204, body: '' });
    } else if (path === `/api/v1/recordings/${RECORDING_ID}/complete` && method === 'POST') {
      const body = (request.postDataJSON() ?? {}) as { name?: string };
      api.completed.push(body);
      if (body.name) projectName = body.name;
      api.saved = true;
      await route.fulfill({ json: project() });
    } else {
      await route.fallback();
    }
  });
  return api;
}

/** Sequences stored for a track, in arrival order. */
function sequences(api: RecordingApi, track: string): number[] {
  return api.chunks.filter((chunk) => chunk.track === track).map((chunk) => chunk.sequence);
}

/** Chooses the fake screen and turns the fake microphone on through the interface. */
async function prepareSources(page: Page, labels: { choose: string; mic: string }): Promise<void> {
  await page.getByRole('button', { name: labels.choose }).click();
  await page.getByRole('button', { name: labels.mic }).click();
}

test.describe('recorder with fake devices', () => {
  test.use({ locale: 'en-US' });

  test('records, pauses, stops and saves with a name without starting processing', async ({
    page,
    context,
  }) => {
    const api = await useRecordingApi(context);
    await page.goto('/projects');
    await page
      .getByRole('navigation', { name: 'Main menu' })
      .getByRole('link', { name: 'Record' })
      .click();
    await expect(page).toHaveURL(/\/record$/);
    await expect(page.getByRole('heading', { name: 'Record', level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start recording' })).toBeDisabled();
    await expect(page.getByText('get their consent')).toBeVisible();

    await prepareSources(page, { choose: 'Choose screen', mic: 'Use microphone' });
    const screenCheck = page.locator('.source-check').nth(0);
    const audioCheck = page.locator('.source-check').nth(1);
    const micCheck = page.locator('.source-check').nth(2);
    await expect(screenCheck).toContainText('Entire screen');
    await expect(audioCheck).toContainText('Included');
    await expect(micCheck).toContainText('On');
    // The fake microphone beeps, so the live gauge must leave 0 within a few readings.
    const gauge = micCheck.getByRole('img', { name: /Microphone level/ });
    await expect
      .poll(
        async () => Number(/(\d+)%/.exec((await gauge.getAttribute('aria-label')) ?? '')?.[1]),
        {
          timeout: 10_000,
          // The fake microphone beeps once a second; poll off that beat so a beep is caught.
          intervals: [130],
        },
      )
      .toBeGreaterThan(0);

    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Recording' })).toBeVisible();
    expect(api.created).toEqual([
      { name: expect.stringMatching(/^Recording \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/) },
    ]);
    await expect
      .poll(() => sequences(api, 'screen').length, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(2);
    await expect
      .poll(() => sequences(api, 'microphone').length, { timeout: 15_000 })
      .toBeGreaterThanOrEqual(2);
    await expect(page.locator('.recording-timer')).not.toHaveText('00:00');

    await page.getByRole('button', { name: 'Pause' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Paused' })).toBeVisible();
    const pausedAt = await page.locator('.recording-timer').textContent();
    await page.waitForTimeout(2_200);
    await expect(page.locator('.recording-timer')).toHaveText(pausedAt ?? '');
    await page.getByRole('button', { name: 'Resume' }).click();
    await page.waitForTimeout(1_500);

    await page.getByRole('button', { name: 'Stop' }).click();
    const nameField = page.getByLabel('Recording name (optional)');
    await expect(nameField).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled();

    // Every track runs from 0 without a gap, starts with the WebM header and carries its offset.
    for (const track of ['screen', 'microphone']) {
      const stored = sequences(api, track).sort((a, b) => a - b);
      expect(stored).toEqual(stored.map((_, index) => index));
      const first = api.chunks.find((chunk) => chunk.track === track && chunk.sequence === 0)!;
      expect(first.head).toEqual(EBML_HEADER);
      expect(
        api.chunks.filter((chunk) => chunk.track === track).every((c) => c.startOffsetMs !== null),
      ).toBe(true);
      expect(first.startOffsetMs).toBeGreaterThanOrEqual(0);
    }

    await nameField.fill('Weekly sync');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText('“Weekly sync” is in Projects as Not processed')).toBeVisible();
    expect(api.completed).toEqual([{ name: 'Weekly sync', stopAtGap: false }]);

    await page.getByRole('link', { name: 'Open projects' }).click();
    const row = page.locator('.project-row').filter({ hasText: 'Weekly sync' });
    await expect(row).toContainText('Not processed');
    expect(api.processRequests).toEqual([]);
  });

  test('asks before leaving a running recording and keeps recording when the user stays', async ({
    page,
    context,
  }) => {
    const api = await useRecordingApi(context);
    await page.goto('/record');
    await prepareSources(page, { choose: 'Choose screen', mic: 'Use microphone' });
    await page.getByRole('button', { name: 'Start recording' }).click();
    await expect.poll(() => api.chunks.length, { timeout: 15_000 }).toBeGreaterThan(0);

    await page
      .getByRole('navigation', { name: 'Main menu' })
      .getByRole('link', { name: 'Projects' })
      .click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Stop recording and leave?');
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(page).toHaveURL(/\/record$/);
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible();

    await page
      .getByRole('navigation', { name: 'Main menu' })
      .getByRole('link', { name: 'Projects' })
      .click();
    await page.getByRole('dialog').getByRole('button', { name: 'Stop and leave' }).click();
    await expect(page).toHaveURL(/\/projects$/);
    expect(api.completed).toEqual([]);
  });

  test('opens an interrupted recording from Projects and saves what was recorded', async ({
    page,
    context,
  }) => {
    const api = await useRecordingApi(context, { interrupted: true });
    await page.goto('/projects');
    const row = page.locator('.project-row').first();
    await expect(row).toContainText('Recording interrupted');
    await row.getByRole('button', { name: /^Open / }).click();
    await expect(page).toHaveURL(/\/record\?recording=rec-1$/);
    await expect(page.getByText('This recording stopped before it was saved')).toBeVisible();
    await expect(page.getByText('3.1 MB')).toBeVisible();

    await page.getByRole('button', { name: 'Save what was recorded' }).click();
    await expect(page.getByText('is in Projects as Not processed')).toBeVisible();
    expect(api.completed).toEqual([{ stopAtGap: true }]);
    expect(api.processRequests).toEqual([]);
  });
});

for (const language of [
  {
    code: 'en',
    locale: 'en-US',
    choose: 'Choose screen',
    mic: 'Use microphone',
    start: 'Start recording',
    stop: 'Stop',
    name: 'Recording name (optional)',
  },
  {
    code: 'it',
    locale: 'it-IT',
    choose: 'Scegli schermo',
    mic: 'Usa microfono',
    start: 'Avvia registrazione',
    stop: 'Ferma',
    name: 'Nome della registrazione (facoltativo)',
  },
] as const) {
  test.describe(`recorder layout in ${language.code === 'en' ? 'English' : 'Italian'}`, () => {
    test.use({ locale: language.locale });

    test('recording and save windows keep contrast and fit 320 px', async ({ page, context }) => {
      await useRecordingApi(context);
      await page.setViewportSize({ width: 320, height: 800 });
      await page.goto('/record');
      await expect(page.locator('html')).toHaveAttribute('lang', language.code);
      await prepareSources(page, language);
      await expect(page.getByRole('button', { name: language.start })).toBeEnabled();
      await assertLayout(page);

      await page.getByRole('button', { name: language.start }).click();
      await expect(page.locator('.recording-timer')).toBeVisible();
      await assertLayout(page);

      await page.getByRole('button', { name: language.stop, exact: true }).click();
      await expect(page.getByLabel(language.name)).toBeVisible();
      await assertLayout(page);
    });
  });
}

/** No horizontal page scroll and no text or control below the contrast minimums. */
async function assertLayout(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  const scan = await scanContrast(page);
  expect(scan.textFailures).toEqual([]);
  expect(scan.controlFailures).toEqual([]);
}
