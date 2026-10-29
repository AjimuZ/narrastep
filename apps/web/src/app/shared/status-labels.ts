// State words and pill tones for projects, processing jobs and exports, shared by every screen.
import { ProcessingJobStatus, ProjectExportStatus, ProjectStatus } from '../core/api.models';
import { StatusTone } from './ui';

/** A state as the status pill shows it: a word (never color alone) and a tone. */
export interface StatusLabel {
  label: string;
  tone: StatusTone;
}

const PROJECT_STATUS: Record<ProjectStatus, StatusLabel> = {
  draft: { label: 'No video', tone: 'info' },
  uploaded: { label: 'Not processed', tone: 'info' },
  queued: { label: 'Queued', tone: 'wait' },
  processing: { label: 'Processing', tone: 'wait' },
  review: { label: 'Ready for review', tone: 'ok' },
  exporting: { label: 'Creating package', tone: 'wait' },
  completed: { label: 'Package ready', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
};

const JOB_STATUS: Record<ProcessingJobStatus, StatusLabel> = {
  queued: { label: 'Queued', tone: 'wait' },
  processing: { label: 'Processing', tone: 'wait' },
  completed: { label: 'Done', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
  cancelling: { label: 'Cancelling', tone: 'wait' },
  cancelled: { label: 'Cancelled', tone: 'info' },
  interrupted: { label: 'Interrupted', tone: 'error' },
};

const EXPORT_STATUS: Record<ProjectExportStatus, StatusLabel> = {
  queued: { label: 'Queued', tone: 'wait' },
  processing: { label: 'Creating', tone: 'wait' },
  completed: { label: 'Ready', tone: 'ok' },
  failed: { label: 'Failed', tone: 'error' },
};

/** Word and tone for a project lifecycle state; every `ProjectStatus` has an entry. */
export function projectStatusLabel(status: ProjectStatus): StatusLabel {
  return PROJECT_STATUS[status];
}

/** Word and tone for a processing job state; every `ProcessingJobStatus` has an entry. */
export function jobStatusLabel(status: ProcessingJobStatus): StatusLabel {
  return JOB_STATUS[status];
}

/** Word and tone for an export package state; every `ProjectExportStatus` has an entry. */
export function exportStatusLabel(status: ProjectExportStatus): StatusLabel {
  return EXPORT_STATUS[status];
}

/** Project status options for the list filter, in lifecycle order, with their words. */
export const PROJECT_STATUS_OPTIONS: readonly { value: ProjectStatus; label: string }[] = (
  Object.keys(PROJECT_STATUS) as ProjectStatus[]
).map((value) => ({ value, label: PROJECT_STATUS[value].label }));
