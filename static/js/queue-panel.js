// Bottom "Queued Files" panel — present on every page (see base.html).
// Browses queued_file by instrument tab + date, edits postfix, inserts
// BLANK-AND-CLEANING runs, and exports the day's Xcalibur sequence CSV.
(function () {
  const panel = document.getElementById('queue-panel');
  if (!panel) return;

  const toggle = document.getElementById('queue-toggle');
  const resize = document.getElementById('queue-resize');
  const dateInput = document.getElementById('queue-date');
  const tabsEl = document.getElementById('queue-tabs');
  const bodyEl = document.getElementById('queue-body');
  const addBlankBtn = document.getElementById('queue-add-blank');
  const exportBtn = document.getElementById('queue-export');
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

  let data = { instruments: [], queues: {}, date: '' };
  let activeInstrument = null;

  // ---- date helpers (local time, to match the server's date.today()) ----
  const pad = (n) => String(n).padStart(2, '0');
  const toInputValue = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const currentYmd = () => (dateInput.value || '').replaceAll('-', '');

  // ---- collapse + height persistence ----
  const COLLAPSE_KEY = 'queuePanelCollapsed';
  const HEIGHT_KEY = 'queuePanelHeight';
  if (localStorage.getItem(COLLAPSE_KEY) === '1') panel.classList.add('collapsed');
  const savedH = localStorage.getItem(HEIGHT_KEY);
  if (savedH) bodyEl.style.setProperty('--queue-body-height', savedH + 'px');

  function syncToggleLabel() {
    const collapsed = panel.classList.contains('collapsed');
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.textContent = (collapsed ? '▲ ' : '▼ ') + 'Queued Files';
  }
  syncToggleLabel();

  toggle.addEventListener('click', function () {
    panel.classList.toggle('collapsed');
    localStorage.setItem(COLLAPSE_KEY, panel.classList.contains('collapsed') ? '1' : '0');
    syncToggleLabel();
  });

  // ---- resize: drag the top strip (up = taller) ----
  resize.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    const startY = e.clientY;
    const startH = bodyEl.getBoundingClientRect().height;
    resize.setPointerCapture(e.pointerId);
    function move(ev) {
      let h = startH - (ev.clientY - startY);
      h = Math.max(80, Math.min(h, window.innerHeight * 0.7));
      bodyEl.style.setProperty('--queue-body-height', h + 'px');
    }
    function up() {
      resize.releasePointerCapture(e.pointerId);
      resize.removeEventListener('pointermove', move);
      resize.removeEventListener('pointerup', up);
      localStorage.setItem(HEIGHT_KEY, Math.round(bodyEl.getBoundingClientRect().height));
    }
    resize.addEventListener('pointermove', move);
    resize.addEventListener('pointerup', up);
  });

  // ---- data ----
  function load() {
    return fetch(`/api/queue?date=${currentYmd()}`)
      .then((r) => r.json())
      .then((d) => { data = d; render(); });
  }

  function post(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payload),
    }).then((r) => {
      if (!r.ok) throw new Error('request failed');
      return r.json();
    }).then((d) => { data = d; render(); });
  }

  // ---- rendering ----
  function render() {
    const instruments = data.instruments || [];
    if (!instruments.includes(activeInstrument)) {
      activeInstrument = instruments[0] || activeInstrument;
    }
    tabsEl.innerHTML = '';
    instruments.forEach((inst) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'queue-tab' + (inst === activeInstrument ? ' active' : '');
      b.textContent = inst;
      b.addEventListener('click', () => { activeInstrument = inst; render(); });
      tabsEl.appendChild(b);
    });
    renderBody();
  }

  function renderBody() {
    const rows = (data.queues && data.queues[activeInstrument]) || [];
    const hasInst = !!activeInstrument;
    exportBtn.disabled = !hasInst || rows.length === 0;

    if (!rows.length) {
      bodyEl.innerHTML = `<p class="queue-empty">${
        hasInst ? 'No runs queued for this day.'
                : 'No instruments yet — queue a batch from a sample page.'
      }</p>`;
      return;
    }

    const table = document.createElement('table');
    table.innerHTML =
      '<thead><tr><th>#</th><th>Filename</th><th>User</th>' +
      '<th>Sample</th><th>Postfix</th><th></th></tr></thead>';
    const tbody = document.createElement('tbody');

    rows.forEach((row) => {
      const tr = document.createElement('tr');
      if (row.is_blank) tr.className = 'queue-blank-row';

      const counter = document.createElement('td');
      counter.textContent = row.daily_counter;
      tr.appendChild(counter);

      const fname = document.createElement('td');
      fname.textContent = row.filename;
      tr.appendChild(fname);

      const user = document.createElement('td');
      user.textContent = row.user_initials || '';
      tr.appendChild(user);

      const samp = document.createElement('td');
      if (row.sample_url) {
        const a = document.createElement('a');
        a.href = row.sample_url;
        a.textContent = `${row.project_code}/${row.experiment_code}/${row.sample_code}`;
        samp.appendChild(a);
      } else {
        samp.textContent = '—';
      }
      tr.appendChild(samp);

      const pf = document.createElement('td');
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'queue-postfix-input';
      inp.value = row.postfix || '';
      function save() {
        const v = inp.value.trim();
        if (v === (row.postfix || '')) return;
        post('/api/queue/update', {
          instrument_initial: activeInstrument,
          date: currentYmd(),
          daily_counter: row.daily_counter,
          postfix: v,
        }).catch(() => { inp.value = row.postfix || ''; });
      }
      inp.addEventListener('blur', save);
      inp.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
      });
      pf.appendChild(inp);
      tr.appendChild(pf);

      const del = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'queue-del';
      btn.title = 'Remove from queue';
      btn.textContent = '×';
      btn.addEventListener('click', () => {
        if (!confirm(`Remove ${row.filename} from the queue?`)) return;
        post('/api/queue/delete', {
          instrument_initial: activeInstrument,
          date: currentYmd(),
          daily_counter: row.daily_counter,
        });
      });
      del.appendChild(btn);
      tr.appendChild(del);

      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    bodyEl.innerHTML = '';
    bodyEl.appendChild(table);
  }

  // ---- controls ----
  dateInput.addEventListener('change', load);
  document.querySelectorAll('[data-queue-day]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const d = new Date();
      if (btn.dataset.queueDay === 'tomorrow') d.setDate(d.getDate() + 1);
      dateInput.value = toInputValue(d);
      load();
    });
  });

  addBlankBtn.addEventListener('click', () => {
    let inst = activeInstrument;
    if (!inst) {
      inst = (prompt('Instrument initial for the blank?') || '').trim();
      if (!inst) return;
      activeInstrument = inst;
    }
    post('/api/queue/blank', { instrument_initial: inst, date: currentYmd() });
  });

  exportBtn.addEventListener('click', () => {
    if (!activeInstrument) return;
    window.location = `/api/queue/csv?instrument=${encodeURIComponent(activeInstrument)}&date=${currentYmd()}`;
  });

  // ---- public API: New Batch calls this after queueing ----
  window.queuePanel = {
    refresh: load,
    show: function (dateYmd, instrument) {
      if (dateYmd) {
        dateInput.value = `${dateYmd.slice(0, 4)}-${dateYmd.slice(4, 6)}-${dateYmd.slice(6, 8)}`;
      }
      if (instrument) activeInstrument = instrument;
      panel.classList.remove('collapsed');
      localStorage.setItem(COLLAPSE_KEY, '0');
      syncToggleLabel();
      return load();
    },
  };

  // ---- init ----
  if (!dateInput.value) dateInput.value = toInputValue(new Date());
  load();
})();
