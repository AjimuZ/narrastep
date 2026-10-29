// Message catalog tool: `check` fails when a message ID exists in one language only, `extract` rewrites the English catalog.
//
// Usage (from apps/web):
//   node scripts/check-i18n.mjs            fast check: source IDs against both catalogs, catalogs against each other
//   node scripts/check-i18n.mjs --extract  also compares messages.en.json with a fresh `ng extract-i18n`
//   node scripts/check-i18n.mjs write      rewrites messages.en.json from the sources (keeps messages.it.json)
//
// Every message must carry a custom ID (`i18n="@@id"`, `i18n-title="@@id"`, `` $localize`:@@id:text` ``) so
// that catalogs stay readable and an edit of the English text never orphans a translation.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const localeDir = join(root, 'src', 'locale');
const LANGUAGES = ['en', 'it'];

/** Reads one catalog as `{ id: text }`; throws a readable error for a missing or malformed file. */
function readCatalog(language) {
  const file = join(localeDir, `messages.${language}.json`);
  const parsed = JSON.parse(readFileSync(file, 'utf8'));
  if (parsed.locale !== language || typeof parsed.translations !== 'object') {
    throw new Error(
      `${relative(root, file)} must be {"locale": "${language}", "translations": {…}}`,
    );
  }
  return parsed.translations;
}

/** Placeholder names (`{$INTERPOLATION}`, `{$START_TAG_SPAN}`…) of a message, sorted, with repeats kept. */
function placeholders(text) {
  return [...text.matchAll(/\{\$([A-Z0-9_]+)\}/g)].map((match) => match[1]).sort();
}

/** All files below `directory` with one of the given extensions. */
function walk(directory, extensions) {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return walk(path, extensions);
    return extensions.some((extension) => name.endsWith(extension)) && !name.endsWith('.spec.ts')
      ? [path]
      : [];
  });
}

/**
 * Message IDs written in the sources, plus the places that use a message without a custom ID.
 * Static text scan: cheap enough to run on every build.
 */
function scanSources() {
  const ids = new Set();
  const problems = [];
  for (const file of walk(join(root, 'src', 'app'), ['.ts', '.html'])) {
    const text = readFileSync(file, 'utf8');
    const name = relative(root, file);
    for (const match of text.matchAll(/\$localize`:([^:`]*):/g)) {
      const id = /@@([A-Za-z0-9_.-]+)$/.exec(match[1])?.[1];
      if (id) ids.add(id);
      else problems.push(`${name}: $localize message without a custom @@id`);
    }
    for (const match of text.matchAll(/\$localize`(?!:)/g)) {
      problems.push(`${name}: $localize message without a custom @@id (offset ${match.index})`);
    }
    for (const match of text.matchAll(/(?<=\s)i18n(?:-[a-z-]+)?(?:="([^"]*)")?(?=[\s>])/g)) {
      const id = /@@([A-Za-z0-9_.-]+)$/.exec(match[1] ?? '')?.[1];
      if (id) ids.add(id);
      else problems.push(`${name}: i18n attribute without a custom @@id`);
    }
  }
  return { ids, problems };
}

/** Runs `ng extract-i18n` into a temporary folder and returns the trimmed English messages. */
function extractEnglish() {
  const output = mkdtempSync(join(tmpdir(), 'narrastep-i18n-'));
  try {
    const result = spawnSync(
      process.execPath,
      [
        join(root, 'node_modules', '@angular', 'cli', 'bin', 'ng.js'),
        'extract-i18n',
        '--output-path',
        output,
      ],
      { cwd: root, encoding: 'utf8' },
    );
    if (result.status !== 0) {
      throw new Error(`ng extract-i18n failed:\n${result.stdout}\n${result.stderr}`);
    }
    const warnings = /WARNINGS:([\s\S]*?)Extraction Complete/.exec(result.stdout)?.[1]?.trim();
    const catalog = JSON.parse(readFileSync(join(output, 'messages.json'), 'utf8')).translations;
    // The template compiler keeps the whitespace around element text; HTML collapses it anyway.
    return {
      messages: Object.fromEntries(Object.entries(catalog).map(([id, text]) => [id, text.trim()])),
      warnings,
    };
  } finally {
    rmSync(output, { recursive: true, force: true });
  }
}

/** Collects every inconsistency between sources and catalogs as readable lines. */
function check({ extract }) {
  const errors = [];
  const catalogs = Object.fromEntries(
    LANGUAGES.map((language) => [language, readCatalog(language)]),
  );
  const { ids: sourceIds, problems } = scanSources();
  errors.push(...problems);

  for (const language of LANGUAGES) {
    const catalog = catalogs[language];
    for (const id of sourceIds) {
      if (!(id in catalog)) errors.push(`messages.${language}.json is missing "${id}"`);
    }
    for (const id of Object.keys(catalog)) {
      if (!sourceIds.has(id))
        errors.push(`messages.${language}.json has "${id}", which no source uses`);
      if (typeof catalog[id] !== 'string' || catalog[id].trim() === '') {
        errors.push(`messages.${language}.json has an empty text for "${id}"`);
      }
    }
  }

  const english = catalogs.en;
  for (const [id, text] of Object.entries(catalogs.it)) {
    if (id in english && placeholders(text).join() !== placeholders(english[id]).join()) {
      errors.push(`"${id}" uses different placeholders in en and it`);
    }
  }

  if (extract) {
    const { messages, warnings } = extractEnglish();
    if (warnings) errors.push(`ng extract-i18n warnings:\n${warnings}`);
    for (const [id, text] of Object.entries(messages)) {
      if (english[id] !== text) {
        errors.push(`messages.en.json is out of date for "${id}"; run "npm run extract-i18n"`);
      }
    }
    for (const id of Object.keys(english)) {
      if (!(id in messages))
        errors.push(`messages.en.json has "${id}", which extraction does not find`);
    }
  }
  return { errors, count: sourceIds.size };
}

const command = process.argv[2];
if (command === 'write') {
  const { messages } = extractEnglish();
  writeFileSync(
    join(localeDir, 'messages.en.json'),
    `${JSON.stringify({ locale: 'en', translations: messages }, null, 2)}\n`,
  );
  console.log(`wrote messages.en.json with ${Object.keys(messages).length} messages`);
} else {
  const { errors, count } = check({ extract: process.argv.includes('--extract') });
  if (errors.length > 0) {
    console.error(errors.join('\n'));
    console.error(`\ni18n check failed: ${errors.length} problem(s)`);
    process.exit(1);
  }
  console.log(`i18n check passed: ${count} messages in ${LANGUAGES.join(' and ')}`);
}
