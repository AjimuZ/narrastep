import { CanDeactivateFn } from '@angular/router';

import { ReviewComponent } from './review.component';

/** Delegates navigation safety to the review workspace that owns pending editor state. */
export const pendingReviewChangesGuard: CanDeactivateFn<ReviewComponent> = (component) =>
  component.canDeactivate();
