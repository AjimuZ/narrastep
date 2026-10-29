// Activity line primitive: a sweeping bar for work whose duration is unknown.
import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Indeterminate on purpose: no value is exposed, so it never implies a percentage. */
@Component({
  selector: 'ns-activity-line',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div class="ns-activity" role="progressbar" [attr.aria-label]="label()"></div>`,
})
export class ActivityLineComponent {
  /** Translated description of the work in progress, for example "Reading the video". */
  readonly label = input.required<string>();
}
