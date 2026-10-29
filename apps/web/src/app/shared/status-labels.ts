// State words and pill tones for projects, processing jobs and exports, shared by every screen.
import { ProcessingJobStatus, ProjectExportStatus, ProjectStatus } from '../core/api.models';
import { StatusTone } from './ui';

/** A state as the status pill shows it: a word (never color alone) and a tone. */
export interface StatusLabel {
  label: string;
  tone: StatusTone;
}

/** Word and tone for a project lifecycle state; every `ProjectStatus` has an entry. */
export function projectStatusLabel(status: ProjectStatus): StatusLabel {
  switch (status) {
    case 'draft':
      return { label: $localize`:@@status.project.draft:No video`, tone: 'info' };
    case 'uploaded':
      return { label: $localize`:@@status.project.uploaded:Not processed`, tone: 'info' };
    case 'queued':
      return { label: $localize`:@@status.project.queued:Queued`, tone: 'wait' };
    case 'processing':
      return { label: $localize`:@@status.project.processing:Processing`, tone: 'wait' };
    case 'review':
      return { label: $localize`:@@status.project.review:Ready for review`, tone: 'ok' };
    case 'exporting':
      return { label: $localize`:@@status.project.exporting:Creating package`, tone: 'wait' };
    case 'completed':
      return { label: $localize`:@@status.project.completed:Package ready`, tone: 'ok' };
    case 'failed':
      return { label: $localize`:@@status.project.failed:Failed`, tone: 'error' };
  }
}

/** Word and tone for a processing job state; every `ProcessingJobStatus` has an entry. */
export function jobStatusLabel(status: ProcessingJobStatus): StatusLabel {
  switch (status) {
    case 'queued':
      return { label: $localize`:@@status.job.queued:Queued`, tone: 'wait' };
    case 'processing':
      return { label: $localize`:@@status.job.processing:Processing`, tone: 'wait' };
    case 'completed':
      return { label: $localize`:@@status.job.completed:Done`, tone: 'ok' };
    case 'failed':
      return { label: $localize`:@@status.job.failed:Failed`, tone: 'error' };
    case 'cancelling':
      return { label: $localize`:@@status.job.cancelling:Cancelling`, tone: 'wait' };
    case 'cancelled':
      return { label: $localize`:@@status.job.cancelled:Cancelled`, tone: 'info' };
    case 'interrupted':
      return { label: $localize`:@@status.job.interrupted:Interrupted`, tone: 'error' };
  }
}

/** Word and tone for an export package state; every `ProjectExportStatus` has an entry. */
export function exportStatusLabel(status: ProjectExportStatus): StatusLabel {
  switch (status) {
    case 'queued':
      return { label: $localize`:@@status.export.queued:Queued`, tone: 'wait' };
    case 'processing':
      return { label: $localize`:@@status.export.processing:Creating`, tone: 'wait' };
    case 'completed':
      return { label: $localize`:@@status.export.completed:Ready`, tone: 'ok' };
    case 'failed':
      return { label: $localize`:@@status.export.failed:Failed`, tone: 'error' };
  }
}

/** Project statuses in lifecycle order, for the list filter. */
const PROJECT_STATUS_ORDER: readonly ProjectStatus[] = [
  'draft',
  'uploaded',
  'queued',
  'processing',
  'review',
  'exporting',
  'completed',
  'failed',
];

/** Project status options for the list filter, in lifecycle order, with their words. */
export function projectStatusOptions(): { value: ProjectStatus; label: string }[] {
  return PROJECT_STATUS_ORDER.map((value) => ({ value, label: projectStatusLabel(value).label }));
}
