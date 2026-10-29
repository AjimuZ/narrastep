// Translates stable API error codes into the active language; unknown codes keep the API's English message.
import { NarrastepApiError } from '../services/api-error.interceptor';
import { activeLocale } from './language';

/**
 * Message for a stable error code (docs/API.md, "Stable errors"), or null when the code has no
 * translation. Called on demand so the text follows the language loaded at startup.
 */
export function errorCodeMessage(code: string): string | null {
  switch (code) {
    case 'PROJECT_NOT_FOUND':
      return $localize`:@@error.PROJECT_NOT_FOUND:This project does not exist. It may have been deleted.`;
    case 'PROJECT_BUSY':
      return $localize`:@@error.PROJECT_BUSY:This project is busy with processing or a package. Wait until it finishes, then try again.`;
    case 'INVALID_PROJECT_STATE':
      return $localize`:@@error.INVALID_PROJECT_STATE:This action is not available in the project's current state.`;
    case 'SOURCE_VIDEO_ALREADY_EXISTS':
      return $localize`:@@error.SOURCE_VIDEO_ALREADY_EXISTS:This project already has a video. A project keeps only one original video.`;
    case 'UPLOAD_TOO_LARGE':
      return $localize`:@@error.UPLOAD_TOO_LARGE:The file is larger than the allowed size. Choose a smaller recording.`;
    case 'UNSUPPORTED_MEDIA_TYPE':
      return $localize`:@@error.UNSUPPORTED_MEDIA_TYPE:This file type is not supported. Choose a video file such as MP4, MOV, MKV or WebM.`;
    case 'VIDEO_VALIDATION_FAILED':
    case 'INVALID_VIDEO':
      return $localize`:@@error.VIDEO_VALIDATION_FAILED:The file is not a valid video. Choose another recording.`;
    case 'VIDEO_STREAM_MISSING':
      return $localize`:@@error.VIDEO_STREAM_MISSING:The file has no video track. Choose a screen recording.`;
    case 'VIDEO_AUDIO_TRACK_MISSING':
      return $localize`:@@error.VIDEO_AUDIO_TRACK_MISSING:The file has no audio track. Choose a recording with sound.`;
    case 'INSUFFICIENT_DISK_SPACE':
      return $localize`:@@error.INSUFFICIENT_DISK_SPACE:There is not enough free disk space. Free some space, then try again.`;
    case 'FFPROBE_FAILED':
      return $localize`:@@error.FFPROBE_FAILED:The video could not be inspected. Choose another recording.`;
    case 'FFMPEG_FAILED':
      return $localize`:@@error.FFMPEG_FAILED:The audio of the video could not be prepared. Retry processing or choose another recording.`;
    case 'TRANSCRIPTION_FAILED':
      return $localize`:@@error.TRANSCRIPTION_FAILED:The speech could not be transcribed on this computer. Check that the transcription model is installed, then retry processing.`;
    case 'FRAME_EXTRACTION_FAILED':
      return $localize`:@@error.FRAME_EXTRACTION_FAILED:The screenshots could not be extracted from the video. Retry processing.`;
    case 'REDIS_UNAVAILABLE':
    case 'WORKER_UNAVAILABLE':
      return $localize`:@@error.WORKER_UNAVAILABLE:The local processing service is not running. Start Narrastep again, then retry.`;
    case 'PROCESSING_INTERRUPTED':
      return $localize`:@@error.PROCESSING_INTERRUPTED:Processing stopped before it finished. Retry processing to start again from the original video.`;
    case 'PROCESSING_CANCELLED':
      return $localize`:@@error.PROCESSING_CANCELLED:Processing was cancelled. The original video is kept.`;
    case 'PROCESSING_FAILED':
      return $localize`:@@error.PROCESSING_FAILED:Processing did not finish. Retry processing to start again from the original video.`;
    case 'PROJECT_ASSET_MISSING':
    case 'MEDIA_FILE_MISSING':
      return $localize`:@@error.PROJECT_ASSET_MISSING:A file of this project is missing on this computer. Upload the video again or restore the project folder.`;
    case 'INVALID_SEGMENT_OPERATION':
      return $localize`:@@error.INVALID_SEGMENT_OPERATION:This change to the segment is not possible. Reload the review and try again.`;
    case 'INVALID_SCREENSHOT_ASSOCIATION':
      return $localize`:@@error.INVALID_SCREENSHOT_ASSOCIATION:This screenshot does not belong to the project of the segment.`;
    case 'INVALID_SCREENSHOT_TIMESTAMP':
      return $localize`:@@error.INVALID_SCREENSHOT_TIMESTAMP:The chosen time is outside the video. Choose a time within its length.`;
    case 'SOURCE_DURATION_UNAVAILABLE':
      return $localize`:@@error.SOURCE_DURATION_UNAVAILABLE:The length of the video is unknown, so an exact frame cannot be taken.`;
    case 'OCR_UNAVAILABLE':
      return $localize`:@@error.OCR_UNAVAILABLE:Text recognition is not available on this computer.`;
    case 'OCR_LANGUAGE_UNAVAILABLE':
      return $localize`:@@error.OCR_LANGUAGE_UNAVAILABLE:The language data for text recognition is not installed.`;
    case 'OCR_FAILED':
      return $localize`:@@error.OCR_FAILED:Text recognition failed. Retry processing.`;
    case 'INVALID_BYTE_RANGE':
      return $localize`:@@error.INVALID_BYTE_RANGE:The video player asked for a part of the file that does not exist. Reload the page.`;
    case 'EXPORT_FAILED':
      return $localize`:@@error.EXPORT_FAILED:The package could not be created. Create it again.`;
    case 'EXPORT_NOT_READY':
      return $localize`:@@error.EXPORT_NOT_READY:The package is not ready yet. Wait until it is finished.`;
    case 'EXPORT_NOT_FOUND':
      return $localize`:@@error.EXPORT_NOT_FOUND:This package does not exist. It may have been deleted.`;
    case 'RECORDING_NOT_FOUND':
      return $localize`:@@error.RECORDING_NOT_FOUND:This recording does not exist. It may have been discarded.`;
    case 'RECORDING_CHUNK_CONFLICT':
      return $localize`:@@error.RECORDING_CHUNK_CONFLICT:A part of this recording was already saved with different content. Stop the recording and save what was recorded.`;
    case 'VALIDATION_ERROR':
      return $localize`:@@error.VALIDATION_ERROR:Some values are not valid. Check the fields and try again.`;
    default:
      return null;
  }
}

/**
 * Text to show for a failure that has a stable `code` and the API's English `apiMessage`.
 * English shows the API message as sent, because it can carry specifics; every other language
 * shows the translation of the code. A code without translation keeps the API message, and
 * `fallback` (already translated) covers a failure that has neither.
 */
export function localizedError(
  code: string | null | undefined,
  apiMessage: string | null | undefined,
  fallback: string,
): string {
  if (apiMessage && activeLocale().toLowerCase().startsWith('en')) {
    return apiMessage;
  }
  return (code ? errorCodeMessage(code) : null) ?? apiMessage ?? fallback;
}

/** Text to show for a failed request: `localizedError` for an API error, else the caller's `fallback`. */
export function errorText(error: unknown, fallback: string): string {
  if (!(error instanceof NarrastepApiError)) {
    return fallback;
  }
  return localizedError(error.code, error.message, fallback);
}
