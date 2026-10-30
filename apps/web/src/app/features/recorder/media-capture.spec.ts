// Unit tests of the recorder capture helpers: level scale, format choice, source kind and browser support.
import { afterEach, describe, expect, it } from 'vitest';

import { FakeTrack, fakeStream, installFakeMedia } from './fake-media.testing';
import {
  MICROPHONE_MIME_TYPES,
  SCREEN_MIME_TYPES_WITH_AUDIO,
  captureSupported,
  desktopWindowSystem,
  levelPercent,
  nextShownLevel,
  pickMimeType,
  screenSourceKind,
} from './media-capture';

describe('levelPercent', () => {
  it('reads silence and no samples as 0', () => {
    expect(levelPercent(new Float32Array(0))).toBe(0);
    expect(levelPercent(new Float32Array(512))).toBe(0);
  });

  it('maps the RMS from -60 dBFS (0) to 0 dBFS (100)', () => {
    expect(levelPercent(new Float32Array(512).fill(1))).toBe(100);
    // RMS 0.1 is -20 dBFS: two thirds of the way from -60 to 0.
    expect(levelPercent(new Float32Array(512).fill(0.1))).toBe(67);
    // RMS 0.001 is -60 dBFS, the floor.
    expect(levelPercent(new Float32Array(512).fill(0.001))).toBe(0);
  });

  it('uses the RMS, so a full-scale sine reads about -3 dBFS', () => {
    const sine = Float32Array.from({ length: 4800 }, (_, index) =>
      Math.sin((2 * Math.PI * 440 * index) / 48_000),
    );
    expect(levelPercent(sine)).toBe(95);
  });
});

describe('nextShownLevel', () => {
  it('rises at once and falls by at most 8 per reading', () => {
    expect(nextShownLevel(10, 60)).toBe(60);
    expect(nextShownLevel(60, 0)).toBe(52);
    expect(nextShownLevel(60, 55)).toBe(55);
    expect(nextShownLevel(5, 0)).toBe(0);
    expect(nextShownLevel(0, 140)).toBe(100);
  });
});

describe('pickMimeType', () => {
  it('returns the first supported format in order of preference', () => {
    const vp8Only = (type: string) => type.startsWith('video/webm;codecs=vp8');
    expect(pickMimeType(SCREEN_MIME_TYPES_WITH_AUDIO, vp8Only)).toBe('video/webm;codecs=vp8,opus');
    expect(pickMimeType(SCREEN_MIME_TYPES_WITH_AUDIO, () => true)).toBe(
      'video/webm;codecs=vp9,opus',
    );
  });

  it('returns null when nothing is supported', () => {
    expect(pickMimeType(MICROPHONE_MIME_TYPES, () => false)).toBeNull();
  });
});

describe('screenSourceKind', () => {
  it('reads the shared surface from the video track', () => {
    for (const surface of ['monitor', 'window', 'browser'] as const) {
      expect(
        screenSourceKind(fakeStream(new FakeTrack('video', { displaySurface: surface }))),
      ).toBe(surface);
    }
    expect(screenSourceKind(fakeStream(new FakeTrack('video')))).toBe('unknown');
    expect(screenSourceKind(fakeStream())).toBe('unknown');
  });
});

describe('desktopWindowSystem', () => {
  const electron = (system: string) =>
    `Mozilla/5.0 (${system}) AppleWebKit/537.36 (KHTML, like Gecko) desktop/0.2.0 Chrome/150.0.0.0 Electron/45.2.0 Safari/537.36`;

  it('names the system of the desktop window', () => {
    expect(desktopWindowSystem(electron('Windows NT 10.0; Win64; x64'))).toBe('windows');
    expect(desktopWindowSystem(electron('Macintosh; Intel Mac OS X 10_15_7'))).toBe('other');
    expect(desktopWindowSystem(electron('X11; Linux x86_64'))).toBe('other');
  });

  it('is null in an ordinary browser, also on Windows', () => {
    expect(
      desktopWindowSystem(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0',
      ),
    ).toBeNull();
    expect(desktopWindowSystem('')).toBeNull();
  });
});

describe('captureSupported', () => {
  let restore: (() => void) | null = null;
  afterEach(() => restore?.());

  it('is false without the capture API and true with it', () => {
    expect(captureSupported()).toBe(false);
    restore = installFakeMedia({
      getDisplayMedia: () => Promise.reject(new Error('unused')),
      getUserMedia: () => Promise.reject(new Error('unused')),
    }).restore;
    expect(captureSupported()).toBe(true);
  });
});
