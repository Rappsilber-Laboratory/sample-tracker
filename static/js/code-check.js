// Live "code already taken" warning for the create forms. Any
// <input data-code-check-url="..."> is checked as the user types: the URL (the
// /api/code-taken endpoint, with every parameter but the code filled in) gets
// &code=<value> appended and replies {taken: bool}. While the code is taken an
// inline error is shown and the form's submit button(s) are disabled. The server
// still rejects duplicates on save, so this is purely early feedback.
(function () {
  document.querySelectorAll('input[data-code-check-url]').forEach(setup);

  function setup(input) {
    const form = input.form;
    const what = input.dataset.codeCheckLabel || 'Code';
    let timer = null;
    let seq = 0;
    const msg = document.createElement('small');
    msg.className = 'field-error code-taken';
    msg.hidden = true;
    input.parentNode.insertBefore(msg, input.nextSibling);

    function setTaken(taken, code) {
      msg.hidden = !taken;
      msg.textContent = taken ? what + ' "' + code + '" is already taken — please choose a different one.' : '';
      input.setAttribute('aria-invalid', taken ? 'true' : 'false');
      if (form) {
        form.querySelectorAll('button[type="submit"], button:not([type])').forEach(function (b) {
          b.disabled = taken;
        });
      }
    }

    function check() {
      const code = input.value.trim();
      const mine = ++seq;
      if (!code) { setTaken(false, code); return; }
      fetch(input.dataset.codeCheckUrl + '&code=' + encodeURIComponent(code), {
        headers: { 'Accept': 'application/json' },
      })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) { if (mine === seq) setTaken(!!data.taken, code); })
        .catch(function () { if (mine === seq) setTaken(false, code); });
    }

    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(check, 250);
    });
    check();
  }
})();
