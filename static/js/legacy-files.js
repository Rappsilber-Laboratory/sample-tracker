(function () {
  const selectionForm = document.getElementById('legacy-selection-form');
  if (selectionForm) {
    const storageKey = selectionForm.dataset.storageKey;
    const hidden = selectionForm.querySelector('[name="selected_ids"]');
    const checkboxes = Array.from(selectionForm.querySelectorAll('[name="file_ids"]'));
    const selectPage = document.getElementById('legacy-select-page');
    const count = document.getElementById('legacy-selection-count');
    let selected = new Set();
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
      if (Array.isArray(saved)) selected = new Set(saved.filter(value => typeof value === 'string'));
    } catch (_) { /* Selection still works if browser storage is unavailable. */ }
    if (selectionForm.dataset.clearSelection === 'true') selected.clear();
    function render() {
      checkboxes.forEach(box => { box.checked = selected.has(box.value); });
      const checked = checkboxes.filter(box => box.checked).length;
      selectPage.checked = checked > 0 && checked === checkboxes.length;
      selectPage.indeterminate = checked > 0 && checked < checkboxes.length;
      hidden.value = JSON.stringify(Array.from(selected));
      count.textContent = selected.size + ' selected (maximum 1,000).';
      document.getElementById('legacy-annotate-button').disabled = selected.size === 0;
      try { sessionStorage.setItem(storageKey, hidden.value); } catch (_) { /* Keep in memory. */ }
    }
    checkboxes.forEach(box => box.addEventListener('change', function () {
      if (box.checked && selected.size >= 1000) {
        box.checked = false;
        count.textContent = 'Maximum 1,000 files. Annotate this batch before selecting more.';
        return;
      }
      if (box.checked) selected.add(box.value);
      else selected.delete(box.value);
      render();
    }));
    selectPage.addEventListener('change', function () {
      const checked = selectPage.checked;
      checkboxes.forEach(box => {
        if (checked && selected.size < 1000) selected.add(box.value);
        else if (!checked) selected.delete(box.value);
      });
      render();
    });
    document.getElementById('legacy-clear-selection').addEventListener('click', function () {
      selected.clear(); render();
    });
    selectionForm.addEventListener('submit', render);
    render();
  }

  const form = document.getElementById('legacy-annotation-form');
  if (form) {
    const tree = JSON.parse(document.getElementById('legacy-experiment-tree').textContent);
    const project = form.querySelector('[name="project"]');
    const experiment = form.querySelector('[name="experiment"]');
    const newProject = document.getElementById('legacy-new-project');
    const newExperiment = document.getElementById('legacy-new-experiment');
    function showNewFields() {
      newProject.hidden = project.value !== '__new__';
      newExperiment.hidden = experiment.value !== '__new__';
    }
    project.addEventListener('change', function () {
      experiment.replaceChildren(new Option('No selection', ''));
      if (project.value) experiment.add(new Option('+ Create new experiment', '__new__'));
      tree.filter(item => item.project === project.value).forEach(item => {
        experiment.add(new Option(item.code + ' — ' + item.name, item.code));
      });
      showNewFields();
    });
    experiment.addEventListener('change', showNewFields);
    showNewFields();
  }
})();
