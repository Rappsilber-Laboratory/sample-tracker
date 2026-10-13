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

    // Well-plate suffix (only affects the filename preview when both the master
    // switch and "add well to filename" are on).
    const wellOn = wellEnable && wellEnable.checked && wellInFilename && wellInFilename.checked;
    let wells = null, startIdx = 0, includePlate = false;
    if (wellOn) {
      wells = makeWellOrder(wellOrder ? wellOrder.value : 'rowwise');
      const startWell = validateWell(wellStart ? wellStart.value : 'A1') || 'A1';
      startIdx = wells.indexOf(startWell);
      includePlate = (Math.floor((startIdx + total - 1) / 96) + 1) > 1;
    }

    const lines = combos.map((combo, idx) => {
      const nnn = pad3(runStart + idx);
      const base = inst + '_' + ymd + '-' + nnn + '_' + proj + '_' + user + '_' + exp;
      const segs = combo.map((v, j) => (active[j].prefix || '') + v + (active[j].postfix || ''));
      if (wellOn) {
        const abs = startIdx + idx;
        const plate = Math.floor(abs / 96) + 1;
        const wname = wells[abs % 96];
        if (includePlate) segs.push('Plate' + plate);
        segs.push(wname);
      }
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
    html += '<ul class="vc-preview-list">' + lines.map((l) => {
      const cls = l.length > MAX_FILENAME_LEN ? ' class="vc-too-long"' : '';
      return '<li' + cls + '>' + escapeHtml(l) + '</li>';
    }).join('') + '</ul>';
    preview.innerHTML = html;
  }

  // Grey out the well sub-options unless the master switch is on.
  function syncWellControls() {
    const on = wellEnable && wellEnable.checked;
    [wellOrder, wellStart, wellInFilename, wellInPosition].forEach(function (el) {
      if (el) el.disabled = !on;
    });
  }

  // ---- init ----
  addBtn.addEventListener('click', function () { addRow(); changed(); });
  [genInstrument, genUser, genDate, genRunStart, codeInput,
   wellEnable, wellOrder, wellStart, wellInFilename, wellInPosition].forEach(function (el) {
    if (!el) return;
    el.addEventListener('input', changed);
    el.addEventListener('change', changed);
  });
  if (wellEnable) wellEnable.addEventListener('change', syncWellControls);
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
