/* chipgen studio — the presentation half.
 *
 * The one rule this file follows: it holds no facts. No list of chips, no
 * directive descriptions, no channel layout, no instrument names. All of
 * that arrives from the engine and is rendered. If you find yourself
 * about to type a hardware fact in here, it belongs in python/studio.py
 * instead — a fact in two places is a fact that will disagree with itself.
 *
 * Two ways to get the contract, and the page works either way:
 *   1. python3 python/serve.py  -> fetched live from /api/manifest
 *   2. python3 python/serve.py --dump studio/studio.json  -> read from disk,
 *      which makes the whole reference usable from file:// with no server.
 * Rendering needs the server, because turning a score into sound needs the
 * emulation. The page says so rather than leaving a dead button.
 */

'use strict';

const state = {
  manifest: null,
  live: false,          // is the engine reachable
  render: null,         // the last successful render
  filter: 'all',
};

const $ = (id) => document.getElementById(id);

/* ---- small helpers --------------------------------------------------- */

function el(tag, attrs = {}, kids = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const kid of [].concat(kids)) {
    if (kid) node.appendChild(typeof kid === 'string'
      ? document.createTextNode(kid) : kid);
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function say(target, kind, text, extra) {
  const box = el('div', { class: `msg ${kind}` }, [el('div', { text })]);
  if (extra) box.appendChild(el('pre', { text: extra }));
  clear(target);
  target.appendChild(box);
}

async function copy(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    // Clipboard access is refused on file:// in several browsers, which
    // is not a failure worth a dialog — fall back to a selection the
    // reader can copy with the keyboard.
    const area = el('textarea', {});
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); } catch (ignored) { /* nothing left */ }
    document.body.removeChild(area);
  }
  if (button) {
    const was = button.textContent;
    button.textContent = 'copied';
    setTimeout(() => { button.textContent = was; }, 900);
  }
}

/* ---- loading the contract -------------------------------------------- */

async function loadManifest() {
  // Live engine first; the dumped file is the fallback, so a page opened
  // from disk still shows everything except rendering.
  try {
    const response = await fetch('/api/manifest', { cache: 'no-store' });
    if (response.ok) {
      state.live = true;
      return await response.json();
    }
  } catch (err) { /* no server — expected when opened from file:// */ }

  try {
    const response = await fetch('studio.json', { cache: 'no-store' });
    if (response.ok) return await response.json();
  } catch (err) { /* neither — reported below */ }
  return null;
}

/* ---- header ---------------------------------------------------------- */

function renderHeader() {
  const m = state.manifest;
  $('tagline').textContent = m.summary;

  const badges = $('badges');
  clear(badges);
  for (const name of Object.keys(m.chips)) {
    badges.appendChild(el('span', { class: 'badge chip', text: name }));
  }

  const health = m.health || {};
  const cores = Object.entries(health.cores || {})
    .map(([chip, kind]) => `${chip} ${kind}`).join('  ·  ');
  if (cores) badges.appendChild(el('span', { class: 'badge', text: cores }));

  if (health.tests) {
    const t = health.tests;
    badges.appendChild(el('span', {
      class: `badge ${t.failed ? 'off' : 'on'}`,
      text: `tests ${t.passed}/${t.total}`,
      title: `last full run ${t.when}`,
    }));
  }
  badges.appendChild(el('span', {
    class: `badge ${state.live ? 'on' : 'off'}`,
    text: state.live ? 'engine connected' : 'engine offline — reference only',
    title: state.live ? '' : 'run: python3 python/serve.py',
  }));
  if (health.numpy === false) {
    badges.appendChild(el('span', {
      class: 'badge', text: 'numpy unavailable — pure-python DSP',
    }));
  }
}

const TABS = [
  ['compose', 'Compose'],
  ['brief', 'Brief a model'],
  ['directives', 'Directives'],
  ['voices', 'Channel bus'],
  ['arrange', 'Rearrange'],
  ['instruments', 'Instruments'],
  ['chips', 'Architecture'],
  ['reference', 'Reference'],
];

function renderTabs() {
  const nav = $('tabs');
  clear(nav);
  TABS.forEach(([id, label], index) => {
    nav.appendChild(el('button', {
      role: 'tab', 'aria-selected': index === 0 ? 'true' : 'false',
      'data-tab': id, text: label,
      onclick: () => selectTab(id),
    }));
  });
}

function selectTab(id, pushHash = true) {
  if (!TABS.some(([tab]) => tab === id)) id = TABS[0][0];
  for (const [tab] of TABS) {
    $(`tab-${tab}`).hidden = tab !== id;
  }
  for (const button of $('tabs').children) {
    button.setAttribute('aria-selected',
      button.dataset.tab === id ? 'true' : 'false');
  }
  // The tab goes in the fragment so a view is linkable: pointing someone
  // at the NES directives should not mean telling them which tab to press.
  if (pushHash && window.location.hash.slice(1) !== id) {
    history.replaceState(null, '', `#${id}`);
  }
}

function tabFromHash() {
  return window.location.hash.slice(1) || TABS[0][0];
}

/* ---- compose --------------------------------------------------------- */

function renderPresets() {
  const select = $('preset-select');
  clear(select);
  select.appendChild(el('option', { value: '', text:
    '— the engine’s built-in example —' }));
  for (const preset of state.manifest.presets) {
    select.appendChild(el('option', {
      value: preset.id,
      text: `${preset.title} — ${preset.chip} · ${preset.bpm} BPM`,
    }));
  }
  select.addEventListener('change', () => showPreset(select.value));
  showPreset('');
}

function showPreset(id) {
  const box = $('preset-detail');
  clear(box);
  if (!id) {
    $('score').value = state.manifest.example;
    box.appendChild(el('p', {
      class: 'muted small',
      text: 'The engine’s own example — short on purpose, and every ' +
            'part of it is something the notation has to get right.',
    }));
    return;
  }
  const preset = state.manifest.presets.find((p) => p.id === id);
  if (!preset) return;

  box.appendChild(el('p', { class: 'muted small', text: preset.summary || '' }));
  box.appendChild(el('div', { class: 'card' }, [
    el('span', { class: 'code', text: preset.prompt }),
    el('div', { class: 'meta' }, [
      el('span', { class: 'tag', text: `${preset.bpm} BPM` }),
      el('span', { class: 'tag', text: preset.key || '' }),
      el('span', { class: 'tag', text: preset.genre || '' }),
      el('button', {
        class: 'pill', text: 'copy prompt',
        onclick: (event) => copy(preset.prompt, event.target),
      }),
    ]),
  ]));

  if (preset.hints && preset.hints.length) {
    const list = el('ul', { class: 'muted small' });
    for (const hint of preset.hints) list.appendChild(el('li', { text: hint }));
    box.appendChild(el('h2', { text: 'What bites on this chip' }));
    box.appendChild(list);
  }

  // A preset describes intent; it does not carry a score. Seed the editor
  // with the voices it names so the columns are right from the start —
  // the rest is the writer's job, or a model's.
  const columns = (preset.voices || []).join(' ');
  $('score').value =
    `; ${preset.title} — ${preset.style || ''}\n` +
    `; ${preset.prompt}\n` +
    `title ${preset.title}\n` +
    `bpm ${preset.bpm}\nlpb 4\n\n` +
    `cols ${columns}\n\n` +
    `; rows go here — one cell per column, ... to hold\n`;
}

async function renderScore() {
  const status = $('render-status');
  const detail = $('render-detail');
  clear(detail);

  if (!state.live) {
    say(status, 'warn',
      'The engine is not running, so there is nothing to render with.',
      'python3 python/serve.py\n\nThen reload this page.');
    return;
  }

  const button = $('render');
  button.disabled = true;
  button.textContent = 'rendering…';
  say(status, 'good', 'rendering…');

  try {
    const response = await fetch('/api/render', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        score: $('score').value,
        chip: $('chip-select').value,
        peak: Number($('peak').value),
        profile: true,
      }),
    });
    const data = await response.json();

    if (data.error && !data.ok) {
      // A typo in a score is the normal way to be wrong. It comes back
      // with a line number, so it is shown as-is rather than wrapped.
      say(status, 'bad', data.kind === 'TrackerError'
        ? 'The score has an error:' : 'Render failed:', data.error);
      return;
    }

    state.render = data;
    $('download-wav').disabled = false;
    $('download-vgm').disabled = false;

    const kind = data.warnings.length ? 'warn' : 'good';
    say(status, kind,
      `${data.events} events · ${data.duration.toFixed(2)} s · ` +
      `peak ${data.peak.toFixed(3)} · rendered in ${data.seconds_to_render} s`);

    showWarnings(detail, data.warnings);
    showProfile(detail, data.profile);

    const player = $('player');
    player.src = data.wav;
    $('player-title').textContent = firstTitle($('score').value) || 'untitled';
    $('player-sub').textContent =
      `${$('chip-select').value} · ${data.duration.toFixed(2)} s · ` +
      `${data.sample_rate} Hz`;
    player.load();
  } catch (err) {
    say(status, 'bad', 'Could not reach the engine.', String(err));
    state.live = false;
    renderHeader();
  } finally {
    button.disabled = false;
    button.textContent = 'Render';
  }
}

function firstTitle(score) {
  const line = score.split('\n').find((l) => l.trim().startsWith('title '));
  return line ? line.trim().slice(6).trim() : '';
}

function showWarnings(target, warnings) {
  if (!warnings || !warnings.length) return;
  const box = el('div', { class: 'msg warn' }, [
    el('div', { text: `${warnings.length} thing${warnings.length > 1 ? 's' : ''} the engine wants to tell you:` }),
  ]);
  const list = el('ul', {});
  for (const warning of warnings) list.appendChild(el('li', { text: warning }));
  box.appendChild(list);
  target.appendChild(box);
}

function showProfile(target, profile) {
  if (!profile || !profile.length) return;
  const table = el('table', {}, [el('tr', {}, [
    el('th', { text: 'section' }), el('th', { text: 'from' }),
    el('th', { text: 'to' }), el('th', { text: 'rms' }),
    el('th', { text: 'peak' }),
  ])]);
  for (const row of profile) {
    table.appendChild(el('tr', {}, [
      el('td', { text: row.label || '—' }),
      el('td', { class: 'num', text: `${row.start.toFixed(1)}s` }),
      el('td', { class: 'num', text: `${row.end.toFixed(1)}s` }),
      el('td', { class: 'num', text: row.rms.toFixed(4) }),
      el('td', { class: 'num', text: row.peak.toFixed(3) }),
    ]));
  }
  target.appendChild(el('h2', { text: 'Section profile' }));
  target.appendChild(el('div', { class: 'scroll' }, [table]));
}

/* ---- brief ----------------------------------------------------------- */

/* The briefing is assembled from manifest.prompts — not written here.
 * An interface that composes its own system prompt teaches a model
 * something different from what the CLI teaches it, and the difference
 * shows up as tracks that render and sound wrong. So this joins the
 * engine's own strings together and adds nothing of its own. */

function briefInputs() {
  return {
    chip: $('brief-chip').value,
    prompt: $('brief-text').value.trim(),
    bpm: Number($('brief-bpm').value) || 150,
    key: $('brief-key').value.trim(),
    style: $('brief-style').value.trim(),
    bars: Number($('brief-bars').value) || 8,
  };
}

function buildBrief() {
  const prompts = state.manifest.prompts || {};
  const chips = prompts.chips || {};
  const input = briefInputs();
  const spec = chips[input.chip];
  if (!spec) { $('brief-out').value = ''; return; }

  const lines = [
    `Write an original chiptune score for the ${input.chip} ` +
    `(${spec.platform}) in chipgen's tracker notation.`,
  ];
  if (input.prompt) lines.push('', `What it should be: ${input.prompt}`);
  lines.push('', 'Specification:', `- tempo: ${input.bpm} BPM`);
  if (input.key) lines.push(`- key: ${input.key}`);
  if (input.style) lines.push(`- style: ${input.style}`);
  lines.push(`- length: ${input.bars} bars`,
             `- columns: \`cols ${spec.columns}\``, '',
             'Hardware that will bite you. Every one of these fails ' +
             'SILENTLY —', 'the render succeeds and the result is wrong:',
             '');
  for (const fact of spec.facts || []) lines.push(`- ${fact}`);
  lines.push('', 'Return the score as tracker notation and nothing else — ' +
             'no explanation around it, no markdown fence. It goes ' +
             'straight into the renderer.');
  $('brief-out').value = lines.join('\n');
}

function renderBrief() {
  const prompts = state.manifest.prompts || {};
  const select = $('brief-chip');
  clear(select);
  for (const name of Object.keys(prompts.chips || {})) {
    select.appendChild(el('option', { value: name, text: name }));
  }
  for (const id of ['brief-chip', 'brief-text', 'brief-bpm', 'brief-key',
                    'brief-style', 'brief-bars']) {
    $(id).addEventListener('input', buildBrief);
    $(id).addEventListener('change', buildBrief);
  }
  $('brief-copy').addEventListener('click',
    (event) => copy($('brief-out').value, event.target));
  $('brief-preset').addEventListener('click', () => {
    const chosen = $('preset-select').value;
    const preset = (state.manifest.presets || []).find((p) => p.id === chosen);
    if (!preset) return;
    $('brief-chip').value = preset.chip;
    $('brief-text').value = preset.prompt;
    $('brief-bpm').value = preset.bpm;
    $('brief-key').value = preset.key || '';
    $('brief-style').value = preset.genre || '';
    $('brief-bars').value = preset.bars || 8;
    buildBrief();
  });
  buildBrief();
}

/* ---- directives ------------------------------------------------------ */

function renderDirectives() {
  const chips = ['all', ...new Set(state.manifest.directives.map((d) => d.chip))];
  const filters = $('directive-filters');
  clear(filters);
  for (const chip of chips) {
    filters.appendChild(el('button', {
      class: 'pill', text: chip,
      'aria-pressed': state.filter === chip ? 'true' : 'false',
      onclick: () => { state.filter = chip; renderDirectives(); },
    }));
  }

  const list = $('directive-list');
  clear(list);
  const shown = state.manifest.directives.filter(
    (d) => state.filter === 'all' || d.chip === state.filter);
  for (const entry of shown) {
    list.appendChild(el('div', { class: 'card' }, [
      el('code', { class: 'code', text: entry.code }),
      el('p', { class: 'desc', text: entry.description }),
      el('div', { class: 'meta' }, [
        el('span', {}, [
          el('span', { class: 'tag', text: entry.chip }),
          el('span', { class: 'tag', text: entry.category }),
        ]),
        el('button', {
          class: 'pill', text: 'copy',
          onclick: (event) => copy(entry.code, event.target),
        }),
      ]),
    ]));
  }
}

/* ---- channel bus ----------------------------------------------------- */

function renderVoices() {
  const list = $('voice-list');
  clear(list);
  for (const voice of state.manifest.voices) {
    list.appendChild(el('div', { class: 'voice' }, [
      el('div', { class: 'name', text: voice.label }),
      el('div', { class: 'col', text: voice.column }),
      el('div', { class: 'role', text: `${voice.chip} · ${voice.role}` }),
      voice.notes ? el('div', { class: 'note', text: voice.notes }) : null,
    ]));
  }
}

/* ---- rearranging ------------------------------------------------------ */

function arrangement() {
  return (state.manifest && state.manifest.arrangement) || null;
}

function renderArrangeTargets() {
  const data = arrangement();
  const select = $('arrange-target');
  clear(select);
  if (!data) {
    say($('arrange-status'), 'warn',
      'This contract predates the arranger, so there is nothing to ' +
      'arrange for.');
    return;
  }
  for (const name of Object.keys(data.targets)) {
    const target = data.targets[name];
    select.appendChild(el('option', {
      value: name,
      text: `${name} — ${target.melodic_voices} melodic` +
            (target.percussion.length
              ? ` + ${target.percussion.length} percussion` : ''),
    }));
  }
  select.addEventListener('change', showArrangeTarget);
  showArrangeTarget();
}

function showArrangeTarget() {
  const data = arrangement();
  const channels = $('arrange-channels');
  const notes = $('arrange-notes');
  clear(channels);
  clear(notes);
  if (!data) return;
  const target = data.targets[$('arrange-target').value];
  if (!target) return;

  const rows = target.channels.map((channel) => el('tr', {}, [
    el('td', {}, [el('code', { class: 'inline', text: channel.column })]),
    el('td', { text: channel.chip }),
    el('td', { text: channel.kind }),
    el('td', { text: data.dynamics[channel.dynamics] || channel.dynamics }),
  ]));
  channels.appendChild(el('div', { class: 'scroll' }, [
    el('table', {}, [
      el('tr', {}, ['column', 'chip', 'kind', 'dynamics']
        .map((h) => el('th', { text: h }))),
      ...rows,
    ]),
  ]));
  for (const channel of target.channels.filter((c) => c.note)) {
    channels.appendChild(el('p', { class: 'note' }, [
      el('code', { class: 'inline', text: channel.column }),
      ` ${channel.note}`,
    ]));
  }

  // What the arranger promises, beside what the chip can hold: the two
  // together are the whole answer to "what does this cost".
  notes.appendChild(el('h3', { text: 'What it will not do quietly' }));
  for (const promise of data.promises) {
    notes.appendChild(el('p', { class: 'note', text: `— ${promise}` }));
  }
}

async function arrangeScore() {
  const status = $('arrange-status');
  const report = $('arrange-report');
  clear(report);

  if (!state.live) {
    say(status, 'warn',
      'Arranging needs the engine: the ranges, the role classifier and ' +
      'the tracker all live there.',
      'python3 python/serve.py\n\nThen reload this page.');
    return;
  }

  const button = $('arrange');
  button.disabled = true;
  button.textContent = 'arranging…';
  try {
    // A chosen MIDI file wins over the editor: picking one is a clear
    // statement about which source you meant.
    const file = ($('arrange-midi').files || [])[0];
    const body = { target: $('arrange-target').value };
    if (file) body.midi = await asBase64(file);
    else body.score = $('score').value;

    const response = await fetch('/api/arrange', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!data.ok) {
      say(status, 'bad', 'Could not arrange that score:', data.error);
      return;
    }

    $('arrange-out').value = data.score;
    $('arrange-use').disabled = false;
    $('arrange-copy').disabled = false;

    const lost = Object.keys(data.report.dropped || {}).length;
    say(status, data.report.lossless ? 'good' : 'warn',
      data.report.lossless
        ? `Fitted onto ${data.target} with nothing lost.`
        : `Fitted onto ${data.target}` +
          (lost ? `, ${lost} voice${lost === 1 ? '' : 's'} dropped.` : '.'));
    const text = (data.imported ? data.imported.join('\n') + '\n\n' : '')
      + data.lines.join('\n');
    report.appendChild(el('pre', { class: 'report', text }));
  } catch (err) {
    say(status, 'bad', 'Could not reach the engine.', String(err));
    state.live = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Arrange';
  }
}

/* ---- instruments ----------------------------------------------------- */

function renderInstruments() {
  const table = $('instrument-table');
  clear(table);
  const instruments = state.manifest.instruments || {};
  const names = Object.keys(instruments);
  table.appendChild(el('tr', {}, [
    el('th', { text: 'patch' }), el('th', { text: 'character' }),
  ]));
  for (const name of names) {
    const value = instruments[name];
    table.appendChild(el('tr', {}, [
      el('td', {}, [el('code', { class: 'inline', text: name })]),
      el('td', { class: 'muted', text:
        typeof value === 'string' ? value : JSON.stringify(value) }),
    ]));
  }

  const samples = $('sample-list');
  clear(samples);
  for (const name of state.manifest.samples || []) {
    samples.appendChild(el('button', {
      class: 'pill', text: name,
      onclick: (event) => copy(name, event.target),
    }));
  }

  const effects = $('effect-table');
  clear(effects);
  const vocabulary = (state.manifest.effects || {}).effects || {};
  effects.appendChild(el('tr', {}, [
    el('th', { text: 'code' }), el('th', { text: 'effect' }),
  ]));
  for (const [code, description] of Object.entries(vocabulary)) {
    effects.appendChild(el('tr', {}, [
      el('td', {}, [el('code', { class: 'inline', text: code })]),
      el('td', { class: 'muted', text: description }),
    ]));
  }
}

/* ---- architecture ---------------------------------------------------- */

function renderChips() {
  const list = $('chip-list');
  clear(list);
  for (const [name, spec] of Object.entries(state.manifest.chips)) {
    const rows = el('table', {});
    for (const [key, value] of Object.entries(spec)) {
      rows.appendChild(el('tr', {}, [
        el('td', { class: 'muted', text: key.replace(/_/g, ' ') }),
        el('td', { text: typeof value === 'object'
          ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('\n')
          : String(value) }),
      ]));
    }
    list.appendChild(el('div', { class: 'panel' }, [
      el('h2', { text: name, style: 'margin-top:0' }),
      el('div', { class: 'scroll' }, [rows]),
    ]));
  }

  const health = $('health-detail');
  clear(health);
  const table = el('table', {});
  for (const [key, value] of Object.entries(state.manifest.health || {})) {
    table.appendChild(el('tr', {}, [
      el('td', { class: 'muted', text: key }),
      el('td', { text: typeof value === 'object' && value !== null
        ? Object.entries(value).map(([k, v]) => `${k}: ${v}`).join('  ·  ')
        : String(value) }),
    ]));
  }
  health.appendChild(table);
}

/* ---- reference ------------------------------------------------------- */

function renderReference() {
  const list = $('reference-list');
  clear(list);
  for (const [topic, body] of Object.entries(state.manifest.reference || {})) {
    const rows = el('table', {});
    const entries = typeof body === 'object' && body !== null
      ? Object.entries(body) : [['', body]];
    for (const [key, value] of entries) {
      rows.appendChild(el('tr', {}, [
        el('td', { class: 'muted', style: 'white-space:nowrap',
                   text: key.replace(/_/g, ' ') }),
        el('td', { text: typeof value === 'object' && value !== null
          ? JSON.stringify(value, null, 1) : String(value) }),
      ]));
    }
    list.appendChild(el('div', { class: 'panel', style: 'margin-bottom:10px' }, [
      el('h2', { text: topic.replace(/_/g, ' '), style: 'margin-top:0' }),
      el('div', { class: 'scroll' }, [rows]),
    ]));
  }
}

/* ---- start ----------------------------------------------------------- */

async function start() {
  const manifest = await loadManifest();
  if (!manifest) {
    document.querySelector('main').prepend(el('div', { class: 'msg bad' }, [
      el('div', { text: 'No contract to render.' }),
      el('pre', { text:
        'Either start the engine:\n' +
        '    python3 python/serve.py\n\n' +
        'or dump the contract so this page works from disk:\n' +
        '    python3 python/serve.py --dump studio/studio.json' }),
    ]));
    $('tagline').textContent = 'no contract';
    return;
  }
  state.manifest = manifest;

  renderHeader();
  renderTabs();
  renderPresets();
  renderBrief();
  renderDirectives();
  renderVoices();
  renderArrangeTargets();
  renderInstruments();
  renderChips();
  renderReference();

  selectTab(tabFromHash(), false);
  window.addEventListener('hashchange', () => selectTab(tabFromHash(), false));

  $('render').addEventListener('click', renderScore);
  $('arrange').addEventListener('click', arrangeScore);
  $('arrange-use').addEventListener('click', () => {
    // Moving the arrangement into the Compose tab rather than rendering
    // it here: one score box, one render path, and the arrangement is
    // then an ordinary score you can edit before you commit to it.
    $('score').value = $('arrange-out').value;
    selectTab('compose');
  });
  $('arrange-copy').addEventListener('click',
    (event) => copy($('arrange-out').value, event.target));
  $('copy-score').addEventListener('click',
    (event) => copy($('score').value, event.target));
  for (const [id, key] of [['download-wav', 'wav'], ['download-vgm', 'vgm']]) {
    $(id).addEventListener('click', () => {
      if (state.render) window.location.href = state.render[key];
    });
  }
}

start();
