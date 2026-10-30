import { expect, test } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { basename, dirname } from 'node:path';
import { inflateRawSync } from 'node:zlib';

/** Exercises the complete browser-to-worker workflow against a disposable local stack. */
test('processes generated media through the real local API and exports reviewed evidence', async ({
  page,
}, testInfo) => {
  const fixturePath = testInfo.outputPath('narrastep-fullstack-fixture.mp4');
  mkdirSync(testInfo.outputDir, { recursive: true });
  generateSyntheticVideo(fixturePath);

  await assertStackIsMockEnabled(page);
  await assertProductionStylesAreActive(page);
  await page.getByLabel('Project name').fill('Full-stack evidence workflow');
  await page.getByText('More options', { exact: true }).click();
  await page
    .getByLabel('Description (optional)')
    .fill('Deterministic local processing and export verification.');
  await page.getByLabel('Spoken language').fill('en');
  await page.getByRole('button', { name: 'Create project' }).click();
  await page.getByLabel('Choose video').setInputFiles(fixturePath);
  await page.getByRole('button', { name: 'Upload video' }).click();
  await expect(page.getByRole('heading', { name: /Ready to process/ })).toBeVisible({
    timeout: 60_000,
  });

  await page.getByRole('button', { name: 'Start processing' }).click();
  const reviewLink = page.getByRole('link', { name: 'Open review' });
  await expect(reviewLink).toBeVisible({ timeout: 180_000 });
  await reviewLink.click();
  await expect(page.getByText('3 segments')).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: testInfo.outputPath('review-workspace.png'), fullPage: true });

  const firstEditor = page.getByLabel('Reviewed text for segment 1');
  const reviewedText = 'Open the search page and review results.';
  await firstEditor.fill(reviewedText);
  await page.locator('article.segment').first().getByRole('button', { name: 'Save text' }).click();
  await expect(page.locator('article.segment').first()).not.toHaveClass(/dirty/);

  const splitCaret = reviewedText.indexOf('and');
  await firstEditor.evaluate(placeTextCaret, splitCaret);
  await page
    .locator('article.segment')
    .first()
    .getByRole('button', { name: 'Split at caret' })
    .click();
  await expect(page.getByRole('heading', { name: 'Split transcript segment' })).toBeVisible();
  await page.getByRole('button', { name: 'Split segment' }).click();
  await expect(page.getByText('4 segments')).toBeVisible();

  const mergeCheckboxes = page.getByLabel(/^Merge segment \d+$/);
  await mergeCheckboxes.nth(0).check();
  await mergeCheckboxes.nth(1).check();
  await page.getByRole('button', { name: 'Merge adjacent' }).click();
  await page.getByRole('button', { name: 'Merge segments' }).click();
  await expect(page.getByText('3 segments')).toBeVisible();

  const primaryAlternatives = page.getByRole('button', { name: 'Use as primary' });
  await expect(primaryAlternatives.first()).toBeVisible({ timeout: 30_000 });
  await primaryAlternatives.first().click();
  await expect(page.getByText('User selected')).toBeVisible();

  await page.getByRole('button', { name: 'Remove primary' }).click();
  await page.getByRole('button', { name: 'Remove screenshot' }).click();
  await expect(page.getByRole('heading', { name: 'No primary screenshot' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Use as primary' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Use as primary' }).first().click();
  await expect(page.getByText('User selected')).toBeVisible();

  await page.getByRole('link', { name: 'Export', exact: true }).click();
  await expect(page.getByText('metadata/manifest.json (schema 0.3.0)')).toBeVisible();
  await page.getByRole('button', { name: 'Create package' }).click();
  const downloadLink = page.getByRole('link', { name: 'Download package' });
  await expect(downloadLink).toBeVisible({ timeout: 120_000 });
  await page.screenshot({ path: testInfo.outputPath('export-history.png'), fullPage: true });

  const [download] = await Promise.all([page.waitForEvent('download'), downloadLink.click()]);
  const exportPath = testInfo.outputPath('narrastep-export.zip');
  expect(download.suggestedFilename()).toMatch(/^\d{4}-\d{2}-\d{2}_[a-z_]+_.+_export-01\.zip$/);
  await download.saveAs(exportPath);
  assertExportPackage(exportPath, reviewedText);
});

/** Confirms that the container stylesheet remains active under the production CSP. */
async function assertProductionStylesAreActive(
  page: import('@playwright/test').Page,
): Promise<void> {
  const contentSecurityPolicyErrors: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error' && message.text().includes('Content Security Policy')) {
      contentSecurityPolicyErrors.push(message.text());
    }
  });

  await page.goto('/projects/new');

  const stylesheet = page.locator('link[rel="stylesheet"]');
  await expect(stylesheet).toHaveCount(1);
  await expect(stylesheet).not.toHaveAttribute('media', 'print');

  const currentMenuEntry = page
    .getByRole('navigation', { name: 'Main menu' })
    .getByRole('link', { name: 'New project' });
  await expect(currentMenuEntry).toHaveAttribute('aria-current', 'page');
  await expect(currentMenuEntry).toHaveCSS('display', 'flex');
  await expect(currentMenuEntry).toHaveCSS('text-decoration-line', 'none');
  await expect(page.getByRole('link', { name: 'Narrastep' })).toHaveCSS(
    'font-family',
    /Pixelify Sans/,
  );
  expect(
    contentSecurityPolicyErrors,
    'The production page must not rely on scripts blocked by the container CSP.',
  ).toEqual([]);
}

/** Places and reports the review caret so Angular derives the exact split portions. */
function placeTextCaret(element: HTMLTextAreaElement, caret: number): void {
  element.focus();
  element.setSelectionRange(caret, caret);
  element.dispatchEvent(new Event('select', { bubbles: true }));
}

/** Fails early when the suite is accidentally pointed at a production-mode local stack. */
async function assertStackIsMockEnabled(page: import('@playwright/test').Page): Promise<void> {
  const response = await page.request.get('/api/v1/health');
  expect(response.ok(), 'The Narrastep API health endpoint must be reachable.').toBe(true);
  const health = (await response.json()) as { database: string; redis: string; worker: string };
  expect(health.database, 'SQLite must be available before the journey starts.').toBe('ok');
  expect(health.redis, 'Redis must be available before the journey starts.').toBe('ok');
  expect(health.worker, 'The Celery worker must be available before the journey starts.').toBe(
    'ok',
  );
  expect(
    process.env['NARRASTEP_E2E_MOCK_CONFIRMED'],
    'Set NARRASTEP_E2E_MOCK_CONFIRMED=true only after starting Compose with mock processing enabled.',
  ).toBe('true');
}

/** Generates a small original MP4 fixture from synthetic video and audio filters. */
function generateSyntheticVideo(destination: string): void {
  const command = process.platform === 'win32' ? 'ffmpeg.exe' : 'ffmpeg';
  const ffmpegArguments = syntheticVideoArguments(destination);
  let result = spawnSync(command, ffmpegArguments, { encoding: 'utf8' });

  // The checked-in backend image supplies the exact FFmpeg dependency for hosts without FFmpeg.
  if (result.error?.message.includes('ENOENT')) {
    const containerDestination = `/fixtures/${basename(destination)}`;
    result = spawnSync(
      'docker',
      [
        'run',
        '--rm',
        '--network',
        'none',
        '--volume',
        `${dirname(destination)}:/fixtures`,
        'localhost/narrastep-backend:0.2.0',
        'ffmpeg',
        ...syntheticVideoArguments(containerDestination),
      ],
      { encoding: 'utf8' },
    );
  }
  expect(
    result.status,
    result.error
      ? `FFmpeg or the built Narrastep backend image is required for the full-stack fixture: ${result.error.message}`
      : `FFmpeg could not generate the full-stack fixture: ${result.stderr}`,
  ).toBe(0);
}

/** Builds deterministic FFmpeg arguments shared by the host and offline container fallback. */
function syntheticVideoArguments(destination: string): string[] {
  return [
    '-hide_banner',
    '-loglevel',
    'error',
    '-y',
    '-f',
    'lavfi',
    '-i',
    'testsrc2=size=640x360:rate=10:duration=11',
    '-f',
    'lavfi',
    '-i',
    'sine=frequency=440:sample_rate=16000:duration=11',
    '-shortest',
    '-c:v',
    'libx264',
    '-preset',
    'veryfast',
    '-pix_fmt',
    'yuv420p',
    '-c:a',
    'aac',
    '-b:a',
    '64k',
    '-movflags',
    '+faststart',
    '-metadata',
    'creation_time=2026-07-15T00:00:00Z',
    destination,
  ];
}

/** Checks required paths and reviewed evidence inside the downloaded immutable ZIP. */
function assertExportPackage(archivePath: string, reviewedText: string): void {
  const entries = stripArchiveRoot(readZipEntries(archivePath));
  const requiredPaths = [
    'README.md',
    'metadata/manifest.json',
    'metadata/source.json',
    '03-transcript/raw-transcript.json',
    '03-transcript/reviewed-transcript.md',
    '03-transcript/subtitles.srt',
    '01-ai-input/llm-input.md',
    '01-ai-input/generation-prompt.md',
    '04-chatgpt-batches/README.md',
    '04-chatgpt-batches/batch-01-of-01/batch-input.md',
  ];
  for (const requiredPath of requiredPaths) {
    expect(entries.has(requiredPath), `Expected ${requiredPath} in the export.`).toBe(true);
  }
  expect(
    [...entries.keys()].some((name) => /^02-screenshots\/step-\d{3}_\d{2}m\d{2}s\.png$/.test(name)),
  ).toBe(true);
  expect(
    [...entries.keys()].some((name) =>
      /^04-chatgpt-batches\/batch-01-of-01\/step-\d{3}_\d{2}m\d{2}s\.png$/.test(name),
    ),
  ).toBe(true);
  expect([...entries.keys()].some((name) => name.startsWith('source/original-video'))).toBe(false);

  const manifest = JSON.parse(
    requiredEntry(entries, 'metadata/manifest.json').toString('utf8'),
  ) as {
    schemaVersion: string;
    segments: Array<{ reviewedText: string }>;
  };
  expect(manifest.schemaVersion).toBe('0.3.0');
  expect(manifest.segments).toHaveLength(3);
  expect(manifest.segments[0]?.reviewedText.replace('\n', '')).toBe(reviewedText);
  expect(requiredEntry(entries, '03-transcript/raw-transcript.json').toString('utf8')).toContain(
    'Deterministic mock transcript segment 1.',
  );
  expect(requiredEntry(entries, '03-transcript/reviewed-transcript.md').toString('utf8')).toContain(
    '../02-screenshots/step-001_',
  );
  expect(requiredEntry(entries, '01-ai-input/generation-prompt.md').toString('utf8')).toContain(
    'Never invent',
  );
  expect(
    requiredEntry(entries, '04-chatgpt-batches/batch-01-of-01/batch-input.md').toString('utf8'),
  ).toContain('Narrastep analysis batch 1 of 1');
}

/** Removes the single archive root folder (ADR 0013) so paths read like the documented layout. */
function stripArchiveRoot(entries: Map<string, Buffer>): Map<string, Buffer> {
  const roots = new Set([...entries.keys()].map((name) => name.split('/')[0]));
  expect(roots.size, 'Expected exactly one archive root folder.').toBe(1);
  const prefix = `${[...roots][0]}/`;
  return new Map([...entries].map(([name, content]) => [name.slice(prefix.length), content]));
}

/** Returns one expected ZIP entry or fails with a focused package-integrity message. */
function requiredEntry(entries: Map<string, Buffer>, name: string): Buffer {
  const content = entries.get(name);
  expect(content, `Expected readable ZIP entry ${name}.`).toBeDefined();
  return content!;
}

/** Reads ordinary ZIP central-directory entries without relying on a system unzip command. */
function readZipEntries(archivePath: string): Map<string, Buffer> {
  const archive = readFileSync(archivePath);
  const centralDirectory = findCentralDirectory(archive);
  const entries = new Map<string, Buffer>();
  let offset = centralDirectory.offset;
  for (let index = 0; index < centralDirectory.count; index += 1) {
    expect(archive.readUInt32LE(offset), 'Invalid ZIP central-directory entry.').toBe(0x02014b50);
    const compressionMethod = archive.readUInt16LE(offset + 10);
    const compressedSize = archive.readUInt32LE(offset + 20);
    const filenameLength = archive.readUInt16LE(offset + 28);
    const extraLength = archive.readUInt16LE(offset + 30);
    const commentLength = archive.readUInt16LE(offset + 32);
    const localHeaderOffset = archive.readUInt32LE(offset + 42);
    const name = archive.subarray(offset + 46, offset + 46 + filenameLength).toString('utf8');
    entries.set(
      name,
      readZipEntry(archive, localHeaderOffset, compressedSize, compressionMethod, name),
    );
    offset += 46 + filenameLength + extraLength + commentLength;
  }
  return entries;
}

/** Locates the bounded end-of-central-directory record for the downloaded ZIP. */
function findCentralDirectory(archive: Buffer): { count: number; offset: number } {
  const minimumOffset = Math.max(0, archive.length - 65_557);
  for (let offset = archive.length - 22; offset >= minimumOffset; offset -= 1) {
    if (archive.readUInt32LE(offset) === 0x06054b50) {
      return {
        count: archive.readUInt16LE(offset + 10),
        offset: archive.readUInt32LE(offset + 16),
      };
    }
  }
  throw new Error('The downloaded file does not contain a valid ZIP central directory.');
}

/** Decompresses one stored or deflated entry using its local-header data offset. */
function readZipEntry(
  archive: Buffer,
  localHeaderOffset: number,
  compressedSize: number,
  compressionMethod: number,
  name: string,
): Buffer {
  expect(archive.readUInt32LE(localHeaderOffset), `Invalid local ZIP header for ${name}.`).toBe(
    0x04034b50,
  );
  const filenameLength = archive.readUInt16LE(localHeaderOffset + 26);
  const extraLength = archive.readUInt16LE(localHeaderOffset + 28);
  const dataOffset = localHeaderOffset + 30 + filenameLength + extraLength;
  const compressed = archive.subarray(dataOffset, dataOffset + compressedSize);
  if (compressionMethod === 0) {
    return compressed;
  }
  if (compressionMethod === 8) {
    return inflateRawSync(compressed);
  }
  throw new Error(`Unsupported ZIP compression method ${compressionMethod} for ${name}.`);
}
