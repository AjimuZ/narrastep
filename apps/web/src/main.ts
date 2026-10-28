import 'zone.js';

import { bootstrapApplication } from '@angular/platform-browser';

import { AppComponent } from './app/app.component';
import { appConfig } from './app/app.config';

bootstrapApplication(AppComponent, appConfig).catch((error: unknown) => {
  // Startup failures stay visible to local developers without exposing user content.
  console.error('Narrastep could not start.', error);
});
