(function () {
  const form = document.getElementById('file-search-form');
  if (!form) return;
  const input = document.getElementById('file-search-input');
  const results = document.getElementById('file-search-results');
  const status = document.getElementById('file-search-status');
  let timer;
  let controller;
  let revision = 0;

  function invalidate() {
    clearTimeout(timer);
    if (controller) controller.abort();
    revision += 1;
    results.replaceChildren();
    results.removeAttribute('aria-busy');
  }

  async function search() {
    invalidate();
    const query = input.value.trim();
    if (!query) {
      status.textContent = 'Type to see suggestions from acquired files.';
      return;
    }
    const currentRevision = revision;
    controller = new AbortController();
    status.textContent = 'Searching…';
    results.setAttribute('aria-busy', 'true');
    try {
      const url = new URL(form.dataset.searchUrl, location.href);
      url.searchParams.set('q', query);
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) throw new Error('Search failed');
      const data = await response.json();
      if (currentRevision !== revision) return;
      data.results.forEach(function (file) {
        const item = document.createElement('li');
        const link = document.createElement('a');
        link.href = file.url;
        const name = document.createElement('strong');
        name.textContent = file.filename;
        const badge = document.createElement('span');
        badge.className = 'file-search-badge';
        badge.textContent = file.status;
        const context = document.createElement('small');
        context.textContent = file.experiment_code
          ? file.project_code + ' · ' + file.project_name + ' / ' +
            file.experiment_code + ' · ' + file.experiment_name
          : 'No associated experiment — open file details';
        link.append(name, badge, context);
        item.append(link);
        results.append(item);
      });
      status.textContent = data.has_more
        ? 'Showing the first 30 matches. Type more of the file name to narrow the results.'
        : data.results.length
          ? data.results.length + ' matching file' + (data.results.length === 1 ? '' : 's') + '. Select a file to open its experiment.'
          : 'No matching files. Try another part of the file name.';
    } catch (error) {
      if (error.name !== 'AbortError' && currentRevision === revision) {
        status.textContent = 'Couldn’t search files. Please try again.';
      }
    } finally {
      if (currentRevision === revision) results.removeAttribute('aria-busy');
    }
  }

  input.addEventListener('input', function () {
    invalidate();
    if (!input.value.trim()) {
      status.textContent = 'Type to see suggestions from acquired files.';
      return;
    }
    status.textContent = 'Searching…';
    timer = setTimeout(search, 200);
  });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    search();
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowDown') {
      const first = results.querySelector('a');
      if (first) {
        event.preventDefault();
        first.focus();
      }
    }
  });
  results.addEventListener('keydown', function (event) {
    const links = Array.from(results.querySelectorAll('a'));
    const index = links.indexOf(document.activeElement);
    if (index < 0) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const next = event.key === 'ArrowDown' ? index + 1 : index - 1;
      if (next < 0) input.focus();
      else if (links[next]) links[next].focus();
    } else if (event.key === 'Escape') {
      input.focus();
    }
  });
  if (input.value.trim()) search();

  const folderInput = document.getElementById('local-folder-input');
  const folderSummary = document.getElementById('local-folder-summary');
  const localInput = document.getElementById('local-search-input');
  const localStatus = document.getElementById('local-search-status');
  const localResults = document.getElementById('local-search-results');
  let localFiles = [];
  let localTimer;

  async function findExperiment(file, button, matches) {
    button.disabled = true;
    matches.textContent = 'Looking up acquired file…';
    try {
      const url = new URL(form.dataset.searchUrl, location.href);
      url.searchParams.set('q', file.name);
      url.searchParams.set('exact', '1');
      const response = await fetch(url);
      if (!response.ok) throw new Error('Lookup failed');
      const data = await response.json();
      matches.replaceChildren();
      if (!data.results.length) {
        matches.textContent = 'No acquired file with this name is registered in the database.';
      }
      data.results.forEach(function (match) {
        const link = document.createElement('a');
        link.href = match.url;
        link.textContent = match.experiment_code
          ? 'Open experiment: ' + match.project_code + ' / ' + match.experiment_code + ' · ' + match.experiment_name
          : 'Open file details (no associated experiment)';
        matches.append(link);
      });
      if (data.has_more) {
        const hint = document.createElement('small');
        hint.textContent = 'Showing the first 30 acquired files with this name.';
        matches.append(hint);
      }
    } catch (error) {
      matches.textContent = 'Couldn’t look up the experiment. Please try again.';
    } finally {
      button.disabled = false;
    }
  }

  function searchLocalFiles() {
    localResults.replaceChildren();
    const query = localInput.value.trim().toLowerCase();
    if (!localFiles.length || !query) {
      localStatus.textContent = localFiles.length
        ? 'Type to search the selected folder.' : 'No files found in the selected folder.';
      return;
    }
    let total = 0;
    const fragment = document.createDocumentFragment();
    localFiles.forEach(function (file) {
      if (!file.name.toLowerCase().includes(query)) return;
      total += 1;
      if (total > 100) return;
      const item = document.createElement('li');
      item.className = 'local-file-result';
      const name = document.createElement('strong');
      name.textContent = file.name;
      const path = document.createElement('small');
      path.textContent = file.webkitRelativePath || file.name;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = 'Find experiment';
      button.setAttribute('aria-label', 'Find experiment for ' + path.textContent);
      const matches = document.createElement('div');
      matches.className = 'local-experiment-matches';
      matches.setAttribute('role', 'status');
      button.addEventListener('click', function () { findExperiment(file, button, matches); });
      item.append(name, path, button, matches);
      fragment.append(item);
    });
    localResults.append(fragment);
    localStatus.textContent = total
      ? (total > 100 ? 'Showing the first 100 of ' + total : total) +
        ' matching local file' + (total === 1 ? '' : 's') +
        (total > 100 ? '. Type more of the name to narrow the results.' : '.')
      : 'No matching files in the selected folder. Try another part of the file name.';
  }

  folderInput.addEventListener('change', function () {
    clearTimeout(localTimer);
    localFiles = Array.from(folderInput.files || []);
    const firstPath = localFiles[0] && localFiles[0].webkitRelativePath;
    const folderName = firstPath ? firstPath.split('/')[0] : 'Selected folder';
    folderSummary.textContent = localFiles.length
      ? folderName + ' · ' + localFiles.length + ' file' + (localFiles.length === 1 ? '' : 's')
      : 'No files found in the selected folder.';
    localInput.disabled = !localFiles.length;
    localInput.placeholder = localFiles.length ? 'Start typing a local file name…' : 'Select a folder first…';
    searchLocalFiles();
    if (localFiles.length) localInput.focus();
  });
  localInput.addEventListener('input', function () {
    clearTimeout(localTimer);
    localResults.replaceChildren();
    localStatus.textContent = 'Searching selected folder…';
    localTimer = setTimeout(searchLocalFiles, 150);
  });
})();
