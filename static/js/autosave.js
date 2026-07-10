// Autosave for edit forms. Any <form data-autosave> persists on every change:
// the whole form is POSTed (debounced) to its own URL with an X-Autosave header,
// reusing the server's normal validation/commit; the route replies with JSON
// {ok} or {ok:false, errors}. A status pill and inline field errors give feedback.
// New-record (create) forms are NOT marked data-autosave — they keep a Create button.
(function () {
  document.querySelectorAll('form[data-autosave]').forEach(setup);

  function setup(form) {
    const status = form.querySelector('.autosave-status');
    let timer = null;

    function setStatus(state, text) {
      if (!status) return;
      status.className = 'autosave-status autosave-' + state;
      status.textContent = text;
    }

    function clearErrors() {
      form.querySelectorAll('.autosave-error').forEach((e) => e.remove());
    }

    function showErrors(errors) {
      clearErrors();
      Object.keys(errors || {}).forEach(function (name) {
        const field = form.querySelector('#' + CSS.escape(name))
          || form.querySelector('[name="' + CSS.escape(name) + '"]');
        (errors[name] || []).forEach(function (msg) {
          const small = document.createElement('small');
          small.className = 'field-error autosave-error';
          small.textContent = msg;
          if (field && field.parentNode) {
            field.parentNode.insertBefore(small, field.nextSibling);
          } else if (status) {
            status.appendChild(small);
          }
        });
      });
    }

    function save() {
      timer = null;
      setStatus('saving', 'Saving…');
      fetch(form.getAttribute('action') || location.href, {
        method: 'POST',
        headers: { 'X-Autosave': '1' },
        body: new FormData(form),
      })
        .then((r) => r.json().catch(() => ({ ok: false })))
        .then((data) => {
          if (data.ok) {
            clearErrors();
            setStatus('saved', 'Saved ✓');
          } else {
            showErrors(data.errors);
            setStatus('error', 'Couldn’t save — check the highlighted fields');
          }
        })
        .catch(() => setStatus('error', 'Couldn’t save (connection problem)'));
    }

    function schedule() {
      setStatus('dirty', 'Editing…');
      if (timer) clearTimeout(timer);
      timer = setTimeout(save, 700);
    }

    form.addEventListener('input', schedule);
    form.addEventListener('change', schedule);
  }
})();
