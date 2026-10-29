// Route guard of the recorder: asks before leaving while a recording runs or parts still upload.
import { CanDeactivateFn } from '@angular/router';

import { RecorderComponent } from './recorder.component';

/** Delegates to the recorder, which owns the recording state and the confirmation. */
export const recorderLeaveGuard: CanDeactivateFn<RecorderComponent> = (component) =>
  component.canDeactivate();
