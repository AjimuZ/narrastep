// Status pill primitive: a project, job, export or model state shown with a colored square and a word.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type StatusTone = 'info' | 'ok' | 'wait' | 'error';

/** The word is required so a state never depends on color alone. */
@Component({
  selector: 'ns-status-pill',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="ns-pill" [class]="'ns-pill ns-pill--' + tone()">{{ label() }}</span>`,
})
export class StatusPillComponent {
  /** Already translated state word, for example "Ready". */
  readonly label = input.required<string>();
  readonly tone = input<StatusTone>('info');
}
