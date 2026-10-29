// Builds the local page the window shows when the Narrastep web app cannot be reached.
'use strict';

/** Colors of `--color-bg`, `--color-text` and `--color-focus-ring` in `apps/web/src/styles/tokens.scss`. */
const COLORS = Object.freeze({ background: '#2d2f42', text: '#f7e6ea', focus: '#f6798c' });

/** Returns `value` with the five HTML-special characters escaped, safe in text and quoted attributes. */
function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/**
 * Returns a `data:text/html` URL of a page that says the app at `entryUrl` is not reachable, in
 * `language` (`'en'` or `'it'`) with `messages` from `messagesFor(language)`. The page has no
 * script and a policy that blocks every resource; its only action is a plain link to `entryUrl`,
 * which the navigation guard allows because it is the app origin.
 */
function buildFallbackPageUrl(language, messages, entryUrl) {
  const url = escapeHtml(entryUrl);
  const body = escapeHtml(messages.unavailableBody).replace('{url}', () => `<code>${url}</code>`);
  const html = `<!doctype html>
<html lang="${language === 'it' ? 'it' : 'en'}">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Narrastep</title>
<style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: ${COLORS.background}; color: ${COLORS.text}; font: 1rem/1.5 system-ui, sans-serif; }
main { max-width: 36rem; padding: 1.5rem; }
h1 { font-size: 1.5rem; margin: 0 0 0.75rem; }
a { color: inherit; font-weight: 700; }
a:focus-visible { outline: 3px solid ${COLORS.focus}; outline-offset: 3px; }
</style>
</head>
<body>
<main>
<h1>${escapeHtml(messages.unavailableTitle)}</h1>
<p>${body}</p>
<p><a href="${url}">${escapeHtml(messages.unavailableRetry)}</a></p>
</main>
</body>
</html>
`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

module.exports = { buildFallbackPageUrl, escapeHtml };
