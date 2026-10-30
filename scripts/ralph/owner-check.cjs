'use strict';
// Guided owner session for the two checks only a person can do: a real Teams call with a headset
// (P05.6) and one window with system audio in the desktop window (P07-CHECK). It reads numbers from
// the local API, never transcript text, and on success records the evidence, commits and pushes.

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline/promises');

const { runnerPaths } = require('./prd.cjs');

const REQUIRED_TASK = 'P07.4b';
const QUESTIONS = [
  'Did the recorder show system audio as Included after you chose the Teams window?',
  'Does the saved video play with both your voice and the sound of the call?',
  'Did the level gauge move when you spoke, and do the You and Others labels look right in Review?',
];

/** Returns the JSON of one API path. Throws with the path when the API refuses or is down. */
async function getJson(base, route) {
  const response = await fetch(`${base}${route}`, { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw new Error(`${route} answered ${response.status}`);
  return response.json();
}

/**
 * Returns the content-free figures of a project's recording. The transcript text passes through
 * this function only to be counted, and is never returned or printed.
 */
function recordingFigures(project, segments) {
  const count = (label) => segments.filter((segment) => segment.speakerLabel === label).length;
  return {
    durationSeconds: Math.round(project.videoAsset?.durationSeconds ?? 0),
    segments: segments.length,
    you: count('you'),
    others: count('others'),
    unlabelled: count(null),
  };
}

/** Returns the roadmap text with the item checked and its dated evidence below it. Pure. */
function tickRoadmap(roadmap, item, evidence) {
  const pattern = new RegExp(`^- \\[ \\] ${item.replace('.', '\\.')}\\b.*$`, 'm');
  if (!pattern.test(roadmap)) throw new Error(`${item} is not an open item of the roadmap`);
  return roadmap.replace(pattern, (line) => `${line.replace('- [ ]', '- [x]')}\n  ${evidence}`);
}

/** Records the passed checks in the documents and the task list, then commits and pushes. */
function recordEvidence(root, figures, date) {
  const paths = runnerPaths(root);
  const summary = `the owner recorded a Teams test call with a headset in the desktop window, choosing one window with system audio: ${figures.durationSeconds} s, ${figures.segments} segments (${figures.you} You, ${figures.others} Others, ${figures.unlabelled} without a label), and answered yes to the system audio, playback and label questions.`;
  const roadmap = fs.readFileSync(paths.roadmap, 'utf8');
  fs.writeFileSync(paths.roadmap, tickRoadmap(roadmap, 'P05.6', `Evidence (${date}): ${summary}`));

  const status = path.join(root, 'docs', 'STATUS.md');
  fs.writeFileSync(status, fs.readFileSync(status, 'utf8').replace(
    '## Last documentation check\n\n',
    `## Last documentation check\n\n${date} (P05.6, P07 manual check): ${summary}\n\n`,
  ));

  const prd = JSON.parse(fs.readFileSync(paths.prd, 'utf8'));
  for (const id of ['P05.6', 'P07-CHECK']) {
    const story = prd.userStories.find((candidate) => candidate.id === id);
    story.passes = true;
    story.verifiedAt = `${date}T00:00:00.000Z`;
  }
  fs.writeFileSync(paths.prd, `${JSON.stringify(prd, null, 2)}\n`);

  const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'inherit' });
  git('add', 'docs/ROADMAP.md', 'docs/STATUS.md', 'scripts/ralph/prd.json');
  git('commit', '--quiet', '-m', 'Record the owner check of a real call in the desktop window', '-m', 'Task: P05.6');
  try {
    git('push', 'origin', 'main');
  } catch {
    process.stdout.write('The commit is saved, but the push failed. The next run pushes it, or run: git push origin main\n');
  }
}

/** Runs the session. Returns the exit status: 0 recorded, 1 a check failed, 2 not ready. */
async function main(root, port) {
  const paths = runnerPaths(root);
  const prd = JSON.parse(fs.readFileSync(paths.prd, 'utf8'));
  const required = prd.userStories.find((story) => story.id === REQUIRED_TASK);
  if (required && !required.passes) {
    process.stdout.write(`Not ready yet: ${REQUIRED_TASK} must pass first, because it enables the microphone and "Save as" in the desktop window.\n`);
    return 2;
  }
  if (prd.userStories.find((story) => story.id === 'P05.6')?.passes) {
    process.stdout.write('This check is already recorded.\n');
    return 0;
  }

  const base = `http://127.0.0.1:${port}/api/v1`;
  try {
    const health = await getJson(base, '/health');
    if (health.status !== 'ok') throw new Error(`status ${health.status}`);
  } catch (error) {
    process.stdout.write(`Start Narrastep first with ./start.sh in another terminal, then run this again (${error.message}).\n`);
    return 2;
  }

  process.stdout.write(`
About 15 minutes. A second person is not needed.

1. Put on the headset and open Teams. In Settings > Devices, choose "Make a test call".
2. In the Narrastep desktop window, open Record. Choose the Teams window, keep system audio on, and
   choose your microphone. Start recording BEFORE the test call starts.
3. Speak during the test call. When it plays your voice back, that is the "Others" side. Say a few
   more sentences of your own afterwards, for about two minutes in all.
4. Stop, save with the name "Owner check", then choose Process with the type Meeting.
5. Wait until the project is Ready for review, and open Review.
`);
  const prompt = readline.createInterface({ input: process.stdin, output: process.stdout });
  await prompt.question('\nPress Enter when the project is Ready for review. ');

  const { items } = await getJson(base, '/projects');
  const project = items.find((candidate) => candidate.name === 'Owner check') ?? items[0];
  if (!project) {
    prompt.close();
    process.stdout.write('No project found. Nothing was recorded.\n');
    return 1;
  }
  const details = await getJson(base, `/projects/${project.id}`);
  const figures = recordingFigures(details, await getJson(base, `/projects/${project.id}/segments`));
  process.stdout.write(`\nFound: ${figures.durationSeconds} s, ${figures.segments} segments, ${figures.you} You, ${figures.others} Others, ${figures.unlabelled} without a label.\n\n`);

  const failed = [];
  for (const question of QUESTIONS) {
    const answer = await prompt.question(`${question} (y/n) `);
    if (!/^y/i.test(answer.trim())) failed.push(question);
  }
  prompt.close();

  if (figures.durationSeconds === 0 || figures.segments === 0) failed.push('The recording has no duration or no segments.');
  if (failed.length > 0) {
    process.stdout.write(`\nNothing was recorded. Tell Claude Code which part failed:\n- ${failed.join('\n- ')}\n`);
    return 1;
  }
  recordEvidence(root, figures, new Date().toISOString().slice(0, 10));
  process.stdout.write('\nRecorded, committed and pushed. You can delete the "Owner check" project.\n');
  return 0;
}

if (require.main === module) {
  const root = process.env.RALPH_ROOT ?? path.resolve(__dirname, '..', '..');
  main(root, process.env.NARRASTEP_WEB_PORT ?? '4200').then(
    (status) => {
      process.exitCode = status;
    },
    (error) => {
      process.stderr.write(`owner-check: ${error.message}\n`);
      process.exitCode = 2;
    },
  );
}

module.exports = { main, recordingFigures, tickRoadmap };
