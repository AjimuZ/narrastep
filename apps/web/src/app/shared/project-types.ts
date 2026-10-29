// The four project types (ADR 0009) with the label and one line of help every screen uses.
import { ProjectMode } from '../core/api.models';

export interface ProjectTypeOption {
  value: ProjectMode;
  label: string;
  /** One sentence that tells the user when to pick this type. */
  help: string;
}

/** All project types in the documented order (docs/UI.md, "Project type labels"), in the active language. */
export function projectTypes(): ProjectTypeOption[] {
  return [
    {
      value: 'task_briefing',
      label: $localize`:@@project_type.task_briefing.label:Task briefing`,
      help: $localize`:@@project_type.task_briefing.help:Someone explains a task or a problem you must solve later.`,
    },
    {
      value: 'meeting',
      label: $localize`:@@project_type.meeting.label:Meeting`,
      help: $localize`:@@project_type.meeting.help:A discussion whose decisions and key points need a summary.`,
    },
    {
      value: 'tutorial',
      label: $localize`:@@project_type.tutorial.label:Tutorial`,
      help: $localize`:@@project_type.tutorial.help:A procedure to repeat or teach, step by step.`,
    },
    {
      value: 'general',
      label: $localize`:@@project_type.general.label:Generic`,
      help: $localize`:@@project_type.general.help:Anything else, such as a demo or a recorded note.`,
    },
  ];
}

/** Label of a project type; every `ProjectMode` has one. */
export function projectTypeLabel(mode: ProjectMode): string {
  return projectTypes().find((type) => type.value === mode)?.label ?? mode;
}
