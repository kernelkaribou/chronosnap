'use strict';
/*
 * Unit tests for the frontend XSS fix (escapeHtml -> escapeAttr in
 * attribute contexts). These tests load the REAL frontend/static/js/app.js
 * source into a Node vm context with a minimal DOM shim, then call the
 * actual global functions to prove:
 *
 *   1. escapeAttr() properly escapes all characters that are dangerous
 *      inside a quoted HTML attribute (& < > " ' \).
 *   2. escapeHtml() only escapes text-node characters (& < >) and does
 *      NOT escape quotes -- this is *why* it was unsafe in attribute
 *      context, and documents the distinction so a future regression is
 *      obvious.
 *   3. Real card/render functions (buildCaptureCardHtml, buildVideoCardHtml)
 *      no longer allow a malicious job/video name to break out of a
 *      `title="..."` attribute.
 *   4. A static regression guard: no `<attr>="...${escapeHtml(`-shaped
 *      pattern remains anywhere in app.js for the specific fields that were
 *      fixed (a future edit that reintroduces escapeHtml in an attribute
 *      position for these fields will fail this test).
 *
 * Run via tests/unit-js/run.sh (executes inside an ephemeral Docker
 * container -- no local Node install required, no dependency added to the
 * shipped frontend).
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// Resolve app.js relative to the repo layout when run locally
// (tests/unit-js/../../frontend/...), or relative to the container mount
// (/frontend/...) when run via run.sh, whichever exists.
const CANDIDATE_PATHS = [
    path.join(__dirname, '..', '..', 'frontend', 'static', 'js', 'app.js'),
    path.join('/frontend', 'static', 'js', 'app.js'),
];
const APP_JS_PATH = CANDIDATE_PATHS.find((p) => fs.existsSync(p));
if (!APP_JS_PATH) {
    console.error(`FATAL: could not locate app.js in any of: ${CANDIDATE_PATHS.join(', ')}`);
    process.exit(2);
}
const appSource = fs.readFileSync(APP_JS_PATH, 'utf8');

let passed = 0;
let failed = 0;
const failures = [];

function test(name, fn) {
    try {
        fn();
        passed += 1;
        console.log(`  PASS  ${name}`);
    } catch (err) {
        failed += 1;
        failures.push({ name, err });
        console.log(`  FAIL  ${name}`);
        console.log(`        ${err.message}`);
    }
}

// --- Minimal DOM shim -------------------------------------------------
// Implements only what app.js touches at *load* time (top-level statements
// run once when the script executes) and what the specific functions under
// test need at call time. Deliberately reimplements the *stable, spec-defined*
// text-node serialization algorithm (escape only & < >) so escapeHtml()'s
// real (unmodified) implementation can run unmodified against it -- this is
// not "reimplementing the code under test", it is the standard, unchanging
// browser text-serialization rule that escapeHtml() itself relies on.
function makeFakeElement() {
    let raw = '';
    return {
        set textContent(v) { raw = String(v); },
        get textContent() { return raw; },
        get innerHTML() {
            return raw
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        },
    };
}

const fakeLocalStorage = {
    _store: {},
    getItem(k) { return Object.prototype.hasOwnProperty.call(this._store, k) ? this._store[k] : null; },
    setItem(k, v) { this._store[k] = String(v); },
    removeItem(k) { delete this._store[k]; },
};

const noop = () => {};

// Generic no-op HTML element stub: covers documentElement/body/any element
// returned by querySelector(All) so that top-level bootstrapping code
// (initTheme(), etc.) can call setAttribute/getAttribute/classList without
// throwing. Kept intentionally dumb -- it does not need to be a real DOM,
// only to not crash when touched.
function makeGenericElement() {
    const attrs = {};
    return {
        classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
        style: {},
        setAttribute: (k, v) => { attrs[k] = String(v); },
        getAttribute: (k) => (Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null),
        removeAttribute: (k) => { delete attrs[k]; },
        addEventListener: noop,
        removeEventListener: noop,
        appendChild: noop,
        contains: () => false,
    };
}

const fakeDocument = {
    createElement: () => makeFakeElement(),
    getElementById: () => null,
    querySelector: () => makeGenericElement(),
    querySelectorAll: () => [],
    addEventListener: noop,
    removeEventListener: noop,
    body: makeGenericElement(),
    documentElement: makeGenericElement(),
};

const sandbox = {
    document: fakeDocument,
    window: {},
    localStorage: fakeLocalStorage,
    sessionStorage: fakeLocalStorage,
    navigator: { userAgent: 'node-test' },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    fetch: () => Promise.reject(new Error('fetch not available in test shim')),
    location: { pathname: '/', search: '', href: 'http://localhost/' },
    history: { pushState: noop, replaceState: noop, back: noop },
    Image: function Image() {},
    URLSearchParams: URLSearchParams,
    matchMedia: () => ({ matches: false, addListener: noop, removeListener: noop, addEventListener: noop }),
    addEventListener: noop,
    removeEventListener: noop,
};
sandbox.window = sandbox; // window === global scope, common browser-script assumption
sandbox.globalThis = sandbox;

vm.createContext(sandbox);

try {
    vm.runInContext(appSource, sandbox, { filename: 'app.js' });
} catch (err) {
    console.error('FATAL: could not load app.js into the test sandbox.');
    console.error(err);
    process.exit(2);
}

// --- 1. escapeAttr() must escape every attribute-breakout character ----
test('escapeAttr escapes double quotes', () => {
    assert.strictEqual(sandbox.escapeAttr('a"b'), 'a&quot;b');
});

test('escapeAttr escapes single quotes', () => {
    assert.strictEqual(sandbox.escapeAttr("a'b"), 'a&#39;b');
});

test('escapeAttr escapes angle brackets', () => {
    assert.strictEqual(sandbox.escapeAttr('<img>'), '&lt;img&gt;');
});

test('escapeAttr escapes ampersands', () => {
    assert.strictEqual(sandbox.escapeAttr('a&b'), 'a&amp;b');
});

test('escapeAttr doubles backslashes (safe for the JS-string-literal-in-onclick context it targets)', () => {
    // escapeAttr's documented purpose is JS string literals inside onclick="..."
    // attributes, so it JS-escapes backslashes (\ -> \\) rather than HTML-entity
    // encoding them; this is intentional, not a gap, since a lone backslash
    // cannot terminate an HTML attribute value on its own.
    assert.strictEqual(sandbox.escapeAttr('a\\b'), 'a\\\\b');
});

test('escapeAttr neutralizes a classic attribute-breakout payload', () => {
    const payload = `x" onmouseover="alert(1)`;
    const escaped = sandbox.escapeAttr(payload);
    assert.ok(!escaped.includes('"'), `expected no raw double-quote in: ${escaped}`);
});

test('escapeAttr neutralizes a single-quote-based breakout payload', () => {
    const payload = `x' onmouseover='alert(1)`;
    const escaped = sandbox.escapeAttr(payload);
    assert.ok(!escaped.includes("'"), `expected no raw single-quote in: ${escaped}`);
});

// --- 2. escapeHtml() documents its narrower (text-node-only) guarantee --
test('escapeHtml escapes angle brackets and ampersands', () => {
    assert.strictEqual(sandbox.escapeHtml('<b>a&b</b>'), '&lt;b&gt;a&amp;b&lt;/b&gt;');
});

test('escapeHtml does NOT escape quotes (why it is unsafe for attributes)', () => {
    const escaped = sandbox.escapeHtml(`x" onmouseover="alert(1)`);
    assert.ok(escaped.includes('"'), 'escapeHtml is expected to leave quotes untouched');
});

// --- 3. Real render functions must not allow attribute breakout --------
function extractAttr(html, attrName) {
    const re = new RegExp(`${attrName}="([^"]*)"`);
    const m = html.match(re);
    return m ? m[1] : null;
}

function htmlUnescape(s) {
    return s
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&amp;/g, '&');
}

test('buildCaptureCardHtml: malicious job_name cannot break out of title="..."', () => {
    const payload = `Evil" onmouseover="alert(document.cookie)`;
    const html = sandbox.buildCaptureCardHtml({ id: 1, job_name: payload, captured_at: '2024-01-01T00:00:00Z' }, '');
    // If the embedded quote were left un-escaped it would terminate the
    // title="..." attribute early, so the regex capture (which stops at the
    // first literal ") would only match up to "Evil" -- not round-trip back
    // to the full original payload. A full, exact round-trip proves the
    // quote was neutralized rather than breaking out of the attribute.
    const titleValue = extractAttr(html, 'title');
    assert.ok(titleValue !== null, 'title attribute should be present');
    assert.ok(htmlUnescape(titleValue).startsWith(payload), `expected full payload to round-trip inside title=, got: ${titleValue}`);
});

test('buildVideoCardHtml: malicious video name cannot break out of title="..."', () => {
    const payload = `Evil"><script>alert(1)</script>`;
    const html = sandbox.buildVideoCardHtml({ id: 1, name: payload, duration_seconds: 5 }, '');
    const titleValue = extractAttr(html, 'title');
    assert.ok(titleValue !== null, 'title attribute should be present');
    assert.ok(htmlUnescape(titleValue) === payload, `expected full payload to round-trip inside title=, got: ${titleValue}`);
    assert.ok(!html.includes('<script>alert(1)</script>'), 'raw <script> must not appear unescaped');
});

// --- 4. Static regression guard ----------------------------------------
// The exact fields fixed in this change; if any of these are ever rewritten
// back to escapeHtml() in an attribute-value position, this test fails.
const FIXED_ATTRIBUTE_PATTERNS = [
    /value="\$\{escapeHtml\(job\.url\)\}"/,
    /data-filename="\$\{escapeHtml\(v\.file_name\)\}"/,
    /data-file="\$\{escapeHtml\(v\.file_name\)\}"/,
    /value="\$\{escapeHtml\(baseName\)\}"/,
    /title="\$\{escapeHtml\(tag\.name\)\}/,
    /value="\$\{escapeHtml\(tag\.name\)\}"/,
    /data-name="\$\{escapeHtml\(tag\.name\.toLowerCase\(\)\)\}"/,
    /title="\$\{escapeHtml\(c\.job_name/,
    /title="\$\{escapeHtml\(v\.name\)\}"/,
];

test('no fixed attribute site has regressed back to escapeHtml()', () => {
    const offenders = FIXED_ATTRIBUTE_PATTERNS.filter((re) => re.test(appSource));
    assert.strictEqual(offenders.length, 0, `regressed patterns found: ${offenders.map(String).join(', ')}`);
});

test('escapeAttr is used at all previously-vulnerable call sites', () => {
    const required = [
        'value="${escapeAttr(job.url)}"',
        'data-filename="${escapeAttr(v.file_name)}"',
        'value="${escapeAttr(baseName)}"',
        'value="${escapeAttr(tag.name)}"',
        'data-name="${escapeAttr(tag.name.toLowerCase())}"',
    ];
    const missing = required.filter((snippet) => !appSource.includes(snippet));
    assert.strictEqual(missing.length, 0, `expected escapeAttr snippets missing from app.js: ${missing.join(', ')}`);
});

// --- Summary -------------------------------------------------------------
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
    process.exit(1);
}
