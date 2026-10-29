import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';

import { ApiErrorEnvelope } from '../api.models';

export class NarrastepApiError extends Error {
  /** Creates the stable frontend error used by feature screens. */
  constructor(
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly status = 0,
  ) {
    super(message);
    this.name = 'NarrastepApiError';
  }
}

/** Converts transport failures into a safe error shape without exposing server internals. */
export const apiErrorInterceptor: HttpInterceptorFn = (request, next) =>
  next(request).pipe(
    catchError((error: unknown) => {
      if (error instanceof NarrastepApiError) {
        return throwError(() => error);
      }

      if (error instanceof HttpErrorResponse) {
        const envelope = readErrorEnvelope(error.error);
        const fallbackMessage =
          error.status === 0
            ? $localize`:@@error.api_unreachable:Narrastep could not reach the local service. Check that Narrastep is running.`
            : $localize`:@@error.request_failed:Narrastep could not complete the request.`;

        return throwError(
          () =>
            new NarrastepApiError(
              envelope?.error.code ?? 'REQUEST_FAILED',
              envelope?.error.message ?? fallbackMessage,
              envelope?.error.details ?? {},
              error.status,
            ),
        );
      }

      return throwError(
        () =>
          new NarrastepApiError(
            'REQUEST_FAILED',
            $localize`:@@error.request_failed:Narrastep could not complete the request.`,
          ),
      );
    }),
  );

/** Reads only the documented error envelope from an untrusted response body. */
function readErrorEnvelope(value: unknown): ApiErrorEnvelope | null {
  if (!isRecord(value) || !isRecord(value['error'])) {
    return null;
  }

  const error = value['error'];
  if (typeof error['code'] !== 'string' || typeof error['message'] !== 'string') {
    return null;
  }

  return {
    error: {
      code: error['code'],
      message: error['message'],
      details: isRecord(error['details']) ? error['details'] : {},
    },
  };
}

/** Narrows an unknown JSON value before properties are inspected. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
