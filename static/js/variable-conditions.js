// Variable-conditions table on the Experiment form: an editable table of
// {description, prefix, values, postfix} rows serialised to a hidden JSON field,
// plus a live filename preview. The Cartesian product of every row's parsed
// value list drives the generated filenames; parseValues() below is the exact
// twin of _parse_condition_values() in app.py, so the preview matches the CSV.
(function () {
  const table = document.getElementById('vc-table');
  if (!table) return;

  const tbody = table.querySelector('tbody');
  const hidden = document.getElementById('varying_conditions');
  const addBtn = document.getElementById('vc-add-row');
  const preview = document.getElementById('vc-preview');
  const container = document.getElementById('variable-conditions');
  const genInstrument = document.getElementById('gen_instrument');
  const genUser = document.getElementById('gen_user');
  const genDate = document.getElementById('gen_date');
  const genRunStart = document.getElementById('gen_run_start');
  const codeInput = document.getElementById('code'); // present on the create form only
  const wellEnable = document.getElementById('well_enable');
  const wellOrder = document.getElementById('well_order');
  const wellStart = document.getElementById('well_start');
  const wellInFilename = document.getElementById('well_in_filename');
  const wellInPosition = document.getElementById('well_in_position');
  const wellTray = document.getElementById('well_tray');
  const extraTraysBox = document.getElementById('well-extra-trays');
  const form = table.closest('form');
  const generateBtn = form && form.querySelector('button[name="action"][value="generate"]');
  let initializing = true;

  const PREVIEW_LIMIT = 200;
  const MAX_RUNS = 10000;
  const MAX_FILENAME_LEN = 180; // mirror of _MAX_FILENAME_LEN in app.py

  // ---- 96-well plate assignment (mirror of the helpers in app.py) ----
  const WELL_ROWS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

  function makeWellOrder(mode) {
    const wells = [];
    if (mode === 'columnwise') {
      for (let c = 1; c <= 12; c++) for (const r of WELL_ROWS) wells.push(r + c);
    } else {
      for (const r of WELL_ROWS) for (let c = 1; c <= 12; c++) wells.push(r + c);
    }
    return wells;
  }

  // ---- autosampler trays (mirror of _TRAYS in app.py) ----
  // One tray per 96-well plate: the "Tray position" select is plate 1; when the
  // runs spill onto more plates, extra "Second/Third/Fourth tray" selects
  // (name="well_extra_tray") appear so each plate's tray can be chosen.
  const TRAYS = ['Y', 'B', 'G', 'R'];
  const TRAY_ORDINALS = ['First', 'Second', 'Third', 'Fourth'];
  // Saved extra trays (everything after the first) seed the selects on load.
  let savedExtraTrays = ((extraTraysBox && extraTraysBox.dataset.trays) || '')
    .split(',').map((t) => t.trim().toUpperCase()).filter((t) => TRAYS.includes(t)).slice(1);

  function extraTraySelects() {
    return extraTraysBox ? Array.from(extraTraysBox.querySelectorAll('select')) : [];
  }

  function currentTrays() {
    return [wellTray ? wellTray.value : TRAYS[0]].concat(extraTraySelects().map((s) => s.value));
  }

  // Show exactly (plates - 1) extra tray selects, keeping chosen values; a new
  // select defaults to the saved value, else the first tray not already used.
  function syncExtraTrays(plates) {
    if (!extraTraysBox) return;
    const want = Math.max(0, Math.min(plates, TRAYS.length) - 1);
    let selects = extraTraySelects();
    if (selects.length === want) return;
    while (selects.length > want) {
      selects.pop().parentNode.remove();
    }
    while (selects.length < want) {
      const n = selects.length + 1; // 1 = second tray
      const used = currentTrays();
      const initial = savedExtraTrays[n - 1] || TRAYS.find((t) => !used.includes(t)) || TRAYS[0];
      const id = 'well_extra_tray_' + (n + 1);
      const span = document.createElement('span');
      const label = document.createElement('label');
      label.htmlFor = id;
      label.textContent = TRAY_ORDINALS[n] + ' tray';
      const sel = document.createElement('select');
      sel.id = id;
      sel.name = 'well_extra_tray';
      TRAYS.forEach(function (t) {
        const opt = document.createElement('option');
        opt.value = t;
        opt.textContent = t;
        if (t === initial) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener('change', function () {
        savedExtraTrays[n - 1] = sel.value;
        changed();
      });
      span.appendChild(label);
      span.appendChild(document.createTextNode(' '));
      span.appendChild(sel);
      extraTraysBox.appendChild(span);
      selects = extraTraySelects();
    }
    // Remember shown values so hiding and re-showing a select keeps its choice.
    extraTraySelects().forEach(function (s, i) { savedExtraTrays[i] = s.value; });
    syncWellControls();
  }

  function validateWell(s) {
    s = (s || '').trim().toUpperCase();
    if (s.length < 2) return null;
    const row = s[0];
    const colText = s.slice(1);
    if (!WELL_ROWS.includes(row) || !/^\d+$/.test(colText)) return null;
    const col = parseInt(colText, 10);
    if (col < 1 || col > 12) return null;
    return row + col;
  }

  // ---- value parsing (mirror of _parse_condition_values in app.py) ----
  const RANGE_RE = /^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*(?:@\s*(\d+(?:\.\d+)?)\s*)?$/;

  function fmtNum(x) {
    if (Number.isInteger(x)) return String(x);
    return String(parseFloat(x.toFixed(10)));
  }

  function parseValues(s) {
    if (!s) return [];
    const m = RANGE_RE.exec(s);
    if (m) {
      const start = parseFloat(m[1]);
      const end = parseFloat(m[2]);
      const step = m[3] ? parseFloat(m[3]) : 1;
      if (step <= 0) return [s.trim()];
      const out = [];
      for (let v = start; v <= end + 1e-9 && out.length <= MAX_RUNS; v += step) {
        out.push(fmtNum(Math.round(v * 1e10) / 1e10));
      }
      return out;
    }
    return s.split(',').map((x) => x.trim()).filter(Boolean);
  }

  function isRange(s) { return RANGE_RE.test(s || ''); }

  // ---- row construction ----
  function makeInput(value, cls) {
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = value || '';
    inp.className = cls;
    return inp;
  }

  function ctlBtn(text, title, fn) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    b.title = title;
    b.className = 'vc-ctl';
    b.addEventListener('click', fn);
    return b;
  }

  function addRow(data) {
    data = data || {};
    const tr = document.createElement('tr');

    const descInp = makeInput(data.description, 'vc-desc');
    const prefixInp = makeInput(data.prefix, 'vc-prefix');
    const valuesInp = makeInput(data.values, 'vc-values');
    const postfixInp = makeInput(data.postfix, 'vc-postfix');

    const descTd = document.createElement('td'); descTd.appendChild(descInp);
    const prefixTd = document.createElement('td'); prefixTd.appendChild(prefixInp);
    const postfixTd = document.createElement('td'); postfixTd.appendChild(postfixInp);

    // Values cell carries an Expand button, shown only while it holds a range.
    const valuesTd = document.createElement('td');
    valuesTd.appendChild(valuesInp);
    const expandBtn = document.createElement('button');
    expandBtn.type = 'button';
    expandBtn.textContent = 'Expand';
    expandBtn.className = 'vc-expand';
    expandBtn.title = 'Expand this range into individual, editable values';
    expandBtn.addEventListener('click', function () {
      valuesInp.value = parseValues(valuesInp.value).join(', ');
      syncExpand();
      changed();
    });
    valuesTd.appendChild(expandBtn);
    function syncExpand() { expandBtn.style.display = isRange(valuesInp.value) ? '' : 'none'; }
    valuesInp.addEventListener('input', syncExpand);
    syncExpand();

    const controls = document.createElement('td');
    controls.className = 'vc-controls';
    controls.append(
      ctlBtn('↑', 'Move up', function () {
        if (tr.previousElementSibling) { tbody.insertBefore(tr, tr.previousElementSibling); changed(); }
      }),
      ctlBtn('↓', 'Move down', function () {
        if (tr.nextElementSibling) { tbody.insertBefore(tr.nextElementSibling, tr); changed(); }
      }),
      ctlBtn('✕', 'Remove row', function () { tr.remove(); changed(); })
    );

    tr.append(descTd, prefixTd, valuesTd, postfixTd, controls);
    [descInp, prefixInp, valuesInp, postfixInp].forEach((i) => i.addEventListener('input', changed));
    tbody.appendChild(tr);
  }

  // ---- serialise + preview ----
  function collect() {
    return Array.from(tbody.querySelectorAll('tr')).map((tr) => ({
      description: tr.querySelector('.vc-desc').value,
      prefix: tr.querySelector('.vc-prefix').value,
      values: tr.querySelector('.vc-values').value,
      postfix: tr.querySelector('.vc-postfix').value,
    }));
  }

  function pad3(n) { return String(n).padStart(3, '0'); }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  let hasLongFilename = false; // set by renderPreview; blocks generation

  function updateGenerateEnabled() {
    if (!generateBtn) return;
    const ready = (genInstrument && genInstrument.value.trim())
      && (genUser && genUser.value.trim())
      && (genDate && genDate.value.trim());
    generateBtn.disabled = !ready || hasLongFilename;
    if (!ready) {
      generateBtn.title = 'Choose an instrument, operator and date first';
    } else if (hasLongFilename) {
      generateBtn.title = 'Some filenames exceed ' + MAX_FILENAME_LEN + ' characters';
    } else {
      generateBtn.title = '';
    }
  }

  function changed() {
    const rows = collect();
    hidden.value = JSON.stringify(rows);
    // Let autosave (which listens on the form) pick up programmatic table edits.
    // Skip during initial hydration so merely opening the page doesn't POST.
    if (!initializing) hidden.dispatchEvent(new Event('change', { bubbles: true }));
    renderPreview(rows);
    updateGenerateEnabled();
  }

  function renderPreview(rows) {
    // With no variable conditions the queue still has one entry: the base
    // filename ending in the experiment code.
    const active = rows.filter((r) => parseValues(r.values).length);
    const lists = active.map((r) => parseValues(r.values));
    const total = lists.reduce((a, l) => a * l.length, 1);

    const inst = ((genInstrument && genInstrument.value) || '').trim() || '⟨inst⟩';
    const user = ((genUser && genUser.value) || '').trim() || '⟨user⟩';
    const ymd = (((genDate && genDate.value) || '').replace(/-/g, '')) || '⟨date⟩';
    const proj = container.dataset.project || '⟨proj⟩';
    const exp = (codeInput ? codeInput.value : container.dataset.exp) || '⟨exp⟩';
    let runStart = parseInt((genRunStart && genRunStart.value), 10);
    if (!Number.isInteger(runStart)) runStart = 1;

    const combos = [];
    (function rec(i, acc) {
      if (combos.length >= PREVIEW_LIMIT) return;
      if (i === lists.length) { combos.push(acc); return; }
      for (const v of lists[i]) {
        if (combos.length >= PREVIEW_LIMIT) break;
        rec(i + 1, acc.concat(v));
      }
    })(0, []);

    // Well-plate assignment: the filename suffix needs the master switch and
    // "add well to filename"; the tray:well position needs "put well in Position".
    const wellsOn = !!(wellEnable && wellEnable.checked);
    const wellInName = wellsOn && wellInFilename && wellInFilename.checked;
    const wellInPos = wellsOn && wellInPosition && wellInPosition.checked;
    let wells = null, startIdx = 0, includePlate = false, plates = 1;
    if (wellsOn) {
      wells = makeWellOrder(wellOrder ? wellOrder.value : 'rowwise');
      const startWell = validateWell(wellStart ? wellStart.value : 'A1') || 'A1';
      startIdx = wells.indexOf(startWell);
      plates = Math.floor((startIdx + total - 1) / 96) + 1;
      includePlate = plates > 1;
    }
    syncExtraTrays(wellInPos ? plates : 1);
    const trays = currentTrays();

    const positions = [];
    const lines = combos.map((combo, idx) => {
      const nnn = pad3(runStart + idx);
      const base = inst + '_' + ymd + '-' + nnn + '_' + proj + '_' + user + '_' + exp;
      const segs = combo.map((v, j) => (active[j].prefix || '') + v + (active[j].postfix || ''));
      let position = '';
      if (wellsOn) {
        const abs = startIdx + idx;
        const plate = Math.floor(abs / 96) + 1;
        const wname = wells[abs % 96];
        if (wellInName) {
          if (includePlate) segs.push('Plate' + plate);
          segs.push(wname);
        }
        if (wellInPos) position = (trays[plate - 1] || '?') + ':' + wname;
      }
      positions.push(position);
      return [base].concat(segs).join('_');
    });

    // Flag filenames that exceed the length Xcalibur/Windows can handle. Only
    // the previewed lines are measured here; the server re-checks every run and
    // refuses to generate the CSV if any is too long.
    const longCount = lines.filter((l) => l.length > MAX_FILENAME_LEN).length;
    hasLongFilename = longCount > 0;
    updateGenerateEnabled();

    let html = '<p class="vc-count">' + total + ' file' + (total === 1 ? '' : 's') +
      (total > PREVIEW_LIMIT ? ' (showing first ' + PREVIEW_LIMIT + ')' : '') + '</p>';
    if (hasLongFilename) {
      html += '<p class="vc-warning">⚠ ' + longCount + ' filename' +
        (longCount === 1 ? '' : 's') + ' exceed ' + MAX_FILENAME_LEN +
        ' characters — shorten the codes, prefixes or postfixes before generating.</p>';
    }
    if (wellInPos && plates > TRAYS.length) {
      html += '<p class="vc-warning">⚠ These runs need ' + plates + ' trays of 96 wells, but the ' +
        'autosampler has only ' + TRAYS.length + ' (' + TRAYS.join(', ') + ').</p>';
    } else if (wellInPos && new Set(trays).size !== trays.length) {
      html += '<p class="vc-warning">⚠ Each tray position must be a different colour.</p>';
    }
    html += '<ul class="vc-preview-list">' + lines.map((l, i) => {
      const cls = l.length > MAX_FILENAME_LEN ? ' class="vc-too-long"' : '';
      const pos = positions[i] ? '<span class="vc-position">' + escapeHtml(positions[i]) + '</span> ' : '';
      return '<li' + cls + '>' + pos + escapeHtml(l) + '</li>';
    }).join('') + '</ul>';
    preview.innerHTML = html;
  }

  // Grey out the well sub-options unless the master switch is on.
  function syncWellControls() {
    const on = wellEnable && wellEnable.checked;
    [wellOrder, wellStart, wellInFilename, wellInPosition].forEach(function (el) {
      if (el) el.disabled = !on;
    });
    // Trays only matter for the Position column.
    const trayOn = on && wellInPosition && wellInPosition.checked;
    [wellTray].concat(extraTraySelects()).forEach(function (el) {
      if (el) el.disabled = !trayOn;
    });
  }

  // ---- init ----
  addBtn.addEventListener('click', function () { addRow(); changed(); });
  [genInstrument, genUser, genDate, genRunStart, codeInput,
   wellEnable, wellOrder, wellStart, wellInFilename, wellInPosition, wellTray].forEach(function (el) {
    if (!el) return;
    el.addEventListener('input', changed);
    el.addEventListener('change', changed);
  });
  if (wellEnable) wellEnable.addEventListener('change', syncWellControls);
  if (wellInPosition) wellInPosition.addEventListener('change', syncWellControls);
  syncWellControls();

  // Enter in a field must not submit the form (autosave persists everything;
  // the only submit is the explicit Generate button). Allow Enter in textareas.
  if (form) {
    form.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') e.preventDefault();
    });
  }

  let initial = [];
  try { initial = JSON.parse(hidden.value || '[]'); } catch (e) { initial = []; }
  if (!Array.isArray(initial) || !initial.length) initial = [{}, {}, {}];
  initial.forEach(addRow);
  changed();
  initializing = false;
  updateGenerateEnabled();
})();
