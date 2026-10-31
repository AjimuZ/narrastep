// Tests that the optional portable build stays outside the default desktop install.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

const root = path.join(__dirname, '..', '..');
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));

describe('portable build wiring', () => {
  it('runs a pinned @electron/packager on demand, writing to the ignored out folder', () => {
    const script = manifest.scripts.package;
    assert.match(script, /@electron\/packager@\d+\.\d+\.\d+\b/);
    assert.match(script, /--out=out\b/);
    assert.match(script, /--ignore=\^\/out/);
  });

  it('does not list the packager as a dependency, so npm ci never installs it', () => {
    for (const field of ['dependencies', 'devDependencies', 'optionalDependencies']) {
      assert.ok(!Object.keys(manifest[field] ?? {}).some((name) => name.includes('packager')));
    }
    const lock = fs.readFileSync(path.join(__dirname, '..', 'package-lock.json'), 'utf8');
    assert.ok(!lock.includes('@electron/packager'));
  });

  it('keeps the output folder out of Git', () => {
    const lines = fs.readFileSync(path.join(root, '.gitignore'), 'utf8').split(/\r?\n/);
    assert.ok(lines.includes('desktop/out/'));
  });
});
