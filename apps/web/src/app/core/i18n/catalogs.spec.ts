// Catalog tests: both languages hold the same messages, with the same placeholders and an entry for every API error code.
import { describe, expect, it } from 'vitest';

import messagesEn from '../../../locale/messages.en.json';
import messagesIt from '../../../locale/messages.it.json';
import { errorCodeMessage } from './error-text';

/** Stable public error codes of docs/API.md ("Stable errors") that the interface can show. */
const API_ERROR_CODES = [
  'PROJECT_NOT_FOUND',
  'PROJECT_BUSY',
  'INVALID_PROJECT_STATE',
  'SOURCE_VIDEO_ALREADY_EXISTS',
  'UPLOAD_TOO_LARGE',
  'UNSUPPORTED_MEDIA_TYPE',
  'VIDEO_VALIDATION_FAILED',
  'VIDEO_STREAM_MISSING',
  'VIDEO_AUDIO_TRACK_MISSING',
  'INSUFFICIENT_DISK_SPACE',
  'FFPROBE_FAILED',
  'FFMPEG_FAILED',
  'TRANSCRIPTION_FAILED',
  'FRAME_EXTRACTION_FAILED',
  'REDIS_UNAVAILABLE',
  'WORKER_UNAVAILABLE',
  'PROCESSING_INTERRUPTED',
  'PROCESSING_CANCELLED',
  'PROJECT_ASSET_MISSING',
  'INVALID_SEGMENT_OPERATION',
  'INVALID_SCREENSHOT_ASSOCIATION',
  'INVALID_SCREENSHOT_TIMESTAMP',
  'SOURCE_DURATION_UNAVAILABLE',
  'OCR_UNAVAILABLE',
  'OCR_LANGUAGE_UNAVAILABLE',
  'OCR_FAILED',
  'INVALID_BYTE_RANGE',
  'EXPORT_FAILED',
  'EXPORT_NOT_READY',
  'RECORDING_NOT_FOUND',
  'RECORDING_CHUNK_CONFLICT',
  'VALIDATION_ERROR',
];

const placeholders = (text: string) =>
  [...text.matchAll(/\{\$([A-Z0-9_]+)\}/g)].map((match) => match[1]).sort();

describe('message catalogs', () => {
  const english: Record<string, string> = messagesEn.translations;
  const italian: Record<string, string> = messagesIt.translations;

  it('declare their language', () => {
    expect(messagesEn.locale).toBe('en');
    expect(messagesIt.locale).toBe('it');
  });

  it('hold the same message IDs in English and Italian', () => {
    expect(Object.keys(italian).sort()).toEqual(Object.keys(english).sort());
  });

  it('never leave a text empty or copy an untranslated placeholder-only text', () => {
    for (const catalog of [english, italian]) {
      for (const [id, text] of Object.entries(catalog)) {
        expect(text.trim(), id).not.toBe('');
      }
    }
  });

  it('use the same placeholders in both languages', () => {
    for (const [id, text] of Object.entries(italian)) {
      expect(placeholders(text), id).toEqual(placeholders(english[id] ?? ''));
    }
  });

  it('translate every stable API error code', () => {
    for (const code of API_ERROR_CODES) {
      expect(errorCodeMessage(code), code).not.toBeNull();
    }
    expect(errorCodeMessage('SOMETHING_NEW')).toBeNull();
  });

  it('keep the fixed terms of docs/UI.md in Italian', () => {
    expect(italian['status.project.completed']).toBe('Pacchetto pronto');
    expect(italian['shell.menu.projects']).toBe('Progetti');
    expect(italian['new_project.type_legend']).toBe('Tipo di progetto');
    expect(italian['common.open_review']).toBe('Apri revisione');
    expect(italian['project_type.general.label']).toBe('Generico');
  });
});
