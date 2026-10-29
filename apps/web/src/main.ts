import 'zone.js';
// The language catalog must be applied before any module evaluates a $localize message.
import './app/core/i18n/init';

import { bootstrapApplication } from '@angular/platform-browser';

import { AppComponent } from './app/app.component';
import { appConfig } from './app/app.config';

bootstrapApplication(AppComponent, appConfig).catch((error: unknown) => {
  // Startup failures stay visible to local developers without exposing user content.
  console.error('Narrastep could not start.', error);
});
