// Microphone policy of the web container (risk K14): pages served with the Permissions-Policy of nginx.conf can ask for the microphone.
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

// The fake devices and these grants leave the Permissions-Policy header as the only possible
// reason for a refusal.
test.use({
  launchOptions: {
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
  },
  permissions: ['microphone', 'camera'],
});

const ALLOWED_POLICY = 'camera=(), microphone=(self), geolocation=()';
const OLD_POLICY = 'camera=(), microphone=(), geolocation=()';

/** Returns every Permissions-Policy value in `apps/web/nginx.conf`; throws when the file is unreadable. */
function nginxPolicies(rootDir: string): string[] {
  const config = readFileSync(join(rootDir, '..', '..', 'nginx.conf'), 'utf8');
  return [...config.matchAll(/add_header\s+Permissions-Policy\s+"([^"]*)"/g)].map(
    (match) => match[1] ?? '',
  );
}

/**
 * Opens a page sent by a real HTTP server on 127.0.0.1, a secure context like the app's own address,
 * with `policy` as its header, and stops the server when `check` has finished.
 */
async function withPolicyPage(
  page: Page,
  policy: string,
  check: () => Promise<void>,
): Promise<void> {
  const server = createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html', 'Permissions-Policy': policy });
    response.end('<!doctype html><title>Policy</title>');
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.goto(`http://127.0.0.1:${(server.address() as AddressInfo).port}/`);
    await check();
  } finally {
    server.closeAllConnections();
    server.close();
  }
}

/** Returns `granted` when `getUserMedia` delivers a stream, otherwise the name of its error. */
function requestMedia(page: Page, constraints: MediaStreamConstraints): Promise<string> {
  return page.evaluate(
    (requested) =>
      navigator.mediaDevices.getUserMedia(requested).then(
        (stream) => {
          stream.getTracks().forEach((track) => track.stop());
          return 'granted';
        },
        (error: Error) => error.name,
      ),
    constraints,
  );
}

test('nginx sends the policy allowing the microphone in every location', ({}, testInfo) => {
  const policies = nginxPolicies(testInfo.config.rootDir);

  expect(policies.length).toBeGreaterThanOrEqual(2);
  expect(new Set(policies)).toEqual(new Set([ALLOWED_POLICY]));
});

test('every policy in nginx.conf lets the page use the microphone', async ({ page }, testInfo) => {
  for (const policy of new Set(nginxPolicies(testInfo.config.rootDir))) {
    await withPolicyPage(page, policy, async () => {
      expect(await requestMedia(page, { audio: true })).toBe('granted');
    });
  }
});

test('the camera stays refused under the policy in nginx.conf', async ({ page }, testInfo) => {
  for (const policy of new Set(nginxPolicies(testInfo.config.rootDir))) {
    await withPolicyPage(page, policy, async () => {
      expect(await requestMedia(page, { video: true })).toBe('NotAllowedError');
    });
  }
});

test('the old policy refuses the microphone', async ({ page }) => {
  await withPolicyPage(page, OLD_POLICY, async () => {
    expect(await requestMedia(page, { audio: true })).toBe('NotAllowedError');
  });
});
