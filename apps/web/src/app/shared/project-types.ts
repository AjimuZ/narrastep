// The four project types (ADR 0009) with the label and one line of help every screen uses.
import { ProjectMode } from '../core/api.models';

export interface ProjectTypeOption {
  value: ProjectMode;
  label: string;
  /** One sentence that tells the user when to pick this type. */
  help: string;
}

/** All project types in the documented order (docs/UI.md, "Project type labels"). */
export const PROJECT_TYPES: readonly ProjectTypeOption[] = [
  {
    value: 'task_briefing',
    label: 'Task briefing',
    help: 'Someone explains a task or a problem you must solve later.',
  },
  {
    value: 'meeting',
    label: 'Meeting',
    help: 'A discussion whose decisions and key points need a summary.',
  },
  {
    value: 'tutorial',
    label: 'Tutorial',
    help: 'A procedure to repeat or teach, step by step.',
  },
  {
    value: 'general',
    label: 'Generic',
    help: 'Anything else, such as a demo or a recorded note.',
  },
];

/** Label of a project type; every `ProjectMode` has one. */
export function projectTypeLabel(mode: ProjectMode): string {
  return PROJECT_TYPES.find((type) => type.value === mode)?.label ?? mode;
}
