(function () {
  "use strict";

  const app = document.getElementById("sdrf-app");
  if (!app) return;

  const requiredHeaders = JSON.parse(document.getElementById("sdrf-required-headers").textContent);
  const acquisitionTerms = JSON.parse(document.getElementById("sdrf-acquisition-terms").textContent);
  const instrumentTerms = JSON.parse(document.getElementById("sdrf-instrument-terms").textContent);
  const automaticColumns = JSON.parse(document.getElementById("sdrf-automatic-columns").textContent);
  const technologyType = app.dataset.technologyType;
  const projectSelect = document.getElementById("sdrf-project");
  const experimentSelect = document.getElementById("sdrf-experiment");
  const filePicker = document.getElementById("sdrf-file-picker");
  const selectAllButton = document.getElementById("sdrf-select-all");
  const clearSelectionButton = document.getElementById("sdrf-clear-selection");
  const loadDatabaseButton = document.getElementById("sdrf-load-database");
  const localFileInput = document.getElementById("sdrf-local-files");
  const dropZone = document.getElementById("sdrf-drop-zone");
  const headRow = document.getElementById("sdrf-head-row");
  const tableBody = document.getElementById("sdrf-table-body");
  const table = document.getElementById("sdrf-table");
  const status = document.getElementById("sdrf-status");
  const errors = document.getElementById("sdrf-errors");
  const columnSelect = document.getElementById("sdrf-column-select");
  const columnSearch = document.getElementById("sdrf-column-search");
  const applyDialog = document.getElementById("sdrf-apply-dialog");
  const applyHeaderLabel = document.getElementById("sdrf-apply-header");
  const applyValue = document.getElementById("sdrf-apply-value");
  const applyHint = document.getElementById("sdrf-apply-hint");
  const applyFiles = document.getElementById("sdrf-apply-files");

  let databaseFiles = [];
  let rows = [];
  let optionalHeaders = [];
  let selectedFileIds = new Set();
  let activeApplyHeader = "";
  const optionalChoices = Array.from(columnSelect.querySelectorAll("option[value]"))
    .filter(option => option.value)
    .map(option => ({
      value: option.value,
      label: option.textContent,
      group: option.parentElement.tagName === "OPTGROUP" ? option.parentElement.label : "Other"
    }));

  function baseName(filename) {
    return filename.replace(/\.[^.]+$/, "");
  }

  function cleanCell(value) {
    return String(value == null ? "" : value).replace(/[\t\r\n]+/g, " ").trim();
  }

  function instrumentKey(value) {
    return cleanCell(value)
      .toLowerCase()
      .replace(/\bthermo(?: fisher)? scientific\b/g, " ")
      .replace(/\bmass spectrometer\b|\bspectrometer\b|\bms\b/g, " ")
      .replace(/\borbitrap$/g, " ")
      .replace(/[^a-z0-9]+/g, "");
  }

  const instrumentsByKey = Object.fromEntries(
    Object.entries(instrumentTerms).map(([name, accession]) => [
      instrumentKey(name), { name, accession }
    ])
  );

  function normalizeControlledTerm(header, value) {
    const cleaned = cleanCell(value);
    if (!cleaned || /^NT=.+;AC=.+/i.test(cleaned)) return cleaned;
    if (header === "comment[proteomics data acquisition method]") {
      return acquisitionTerms[cleaned.toLowerCase()] || cleaned;
    }
    if (header === "comment[instrument]") {
      const match = instrumentsByKey[instrumentKey(cleaned)];
      return match ? `NT=${match.name};AC=${match.accession}` : cleaned;
    }
    return cleaned;
  }

  function normalizeAllControlledTerms() {
    rows.forEach(row => {
      ["comment[proteomics data acquisition method]", "comment[instrument]"].forEach(header => {
        row.values[header] = normalizeControlledTerm(header, row.values[header]);
      });
    });
  }

  function localDraft(file, index) {
    const name = file.name || `file-${index + 1}`;
    const stem = baseName(name);
    return {
      key: `local-${Date.now()}-${index}-${name}`,
      optional: {},
      values: {
        "source name": stem,
        "characteristics[organism]": "",
        "characteristics[organism part]": "not available",
        "characteristics[biological replicate]": "1",
        "assay name": stem,
        "technology type": technologyType,
        "comment[proteomics data acquisition method]": "",
        "comment[label]": "",
        "comment[instrument]": "",
        "comment[cleavage agent details]": "",
        "comment[fraction identifier]": "1",
        "comment[technical replicate]": "1",
        "comment[data file]": name
      }
    };
  }

  function setStatus(message, kind) {
    status.textContent = message;
    status.classList.toggle("has-errors", kind === "error");
  }

  function showErrors(messages) {
    errors.replaceChildren();
    if (!messages.length) {
      errors.hidden = true;
      return;
    }
    const strong = document.createElement("strong");
    strong.textContent = "Please complete the highlighted required cells before exporting.";
    const list = document.createElement("ul");
    messages.slice(0, 20).forEach(message => {
      const item = document.createElement("li");
      item.textContent = message;
      list.appendChild(item);
    });
    if (messages.length > 20) {
      const item = document.createElement("li");
      item.textContent = `…and ${messages.length - 20} more.`;
      list.appendChild(item);
    }
    errors.append(strong, list);
    errors.hidden = false;
  }

  function allHeaders() {
    return requiredHeaders.concat(optionalHeaders);
  }

  function exportHeaders() {
    // SDRF characteristics belong between source name and assay name, even
    // though optional columns stay at the right edge in the editing table.
    const characteristics = optionalHeaders.filter(header => header.startsWith("characteristics["));
    const factors = optionalHeaders.filter(header => header.startsWith("factor value["));
    const other = optionalHeaders.filter(header => !characteristics.includes(header) && !factors.includes(header));
    const assayIndex = requiredHeaders.indexOf("assay name");
    return [
      ...requiredHeaders.slice(0, assayIndex),
      ...characteristics,
      ...requiredHeaders.slice(assayIndex),
      ...Object.keys(automaticColumns),
      ...other,
      ...factors
    ];
  }

  function createCell(row, header, required) {
    const cell = document.createElement("td");
    cell.contentEditable = "true";
    cell.spellcheck = false;
    cell.dataset.header = header;
    cell.dataset.placeholder = required ? "Required" : "Optional";
    cell.textContent = row.values[header] || row.optional[header] || "";
    if (required) cell.classList.add("sdrf-required-cell");
    if (header === "technology type" || header === "comment[data file]") {
      cell.classList.add("sdrf-derived-cell");
    }
    return cell;
  }

  function renderTable() {
    tableBody.replaceChildren();
    if (!rows.length) {
      const tr = document.createElement("tr");
      tr.className = "sdrf-placeholder";
      const td = document.createElement("td");
      td.colSpan = allHeaders().length + 2;
      td.textContent = "Load one or more files to start editing.";
      tr.appendChild(td);
      tableBody.appendChild(tr);
      setStatus("No files loaded");
      return;
    }

    rows.forEach((row, rowIndex) => {
      const tr = document.createElement("tr");
      tr.dataset.rowIndex = rowIndex;
      const number = document.createElement("th");
      number.scope = "row";
      number.className = "sdrf-row-number";
      number.textContent = String(rowIndex + 1);
      tr.appendChild(number);
      requiredHeaders.forEach(header => tr.appendChild(createCell(row, header, true)));
      optionalHeaders.forEach(header => tr.appendChild(createCell(row, header, false)));
      const action = document.createElement("td");
      action.className = "sdrf-row-action";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "sdrf-remove-row";
      remove.dataset.removeRow = rowIndex;
      remove.title = "Remove this file";
      remove.setAttribute("aria-label", `Remove row ${rowIndex + 1}`);
      remove.textContent = "×";
      action.appendChild(remove);
      tr.appendChild(action);
      tableBody.appendChild(tr);
    });
    setStatus(`${rows.length} file${rows.length === 1 ? "" : "s"} loaded · ${allHeaders().length} columns`);
  }

  function syncCell(cell) {
    const tr = cell.closest("tr[data-row-index]");
    if (!tr) return;
    const row = rows[Number(tr.dataset.rowIndex)];
    row.values[cell.dataset.header] = cleanCell(cell.textContent);
    cell.classList.remove("sdrf-invalid");
  }

  function normalizeCell(cell) {
    const tr = cell.closest("tr[data-row-index]");
    if (!tr) return;
    const row = rows[Number(tr.dataset.rowIndex)];
    const header = cell.dataset.header;
    const value = normalizeControlledTerm(header, cell.textContent);
    row.values[header] = value;
    cell.textContent = value;
    cell.classList.remove("sdrf-invalid");
  }

  function addOptionalColumn(header) {
    if (!header || requiredHeaders.includes(header) || optionalHeaders.includes(header)) return;
    optionalHeaders.push(header);
    rows.forEach(row => {
      row.values[header] = row.optional[header] || "";
    });

    const actionHeader = headRow.querySelector(".sdrf-row-action");
    const th = document.createElement("th");
    th.dataset.header = header;
    th.className = "sdrf-optional-header";
    const label = document.createElement("span");
    label.textContent = header;
    const apply = document.createElement("button");
    apply.type = "button";
    apply.className = "sdrf-apply-column";
    apply.dataset.applyColumn = header;
    apply.textContent = "Apply to files…";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "sdrf-remove-column";
    remove.dataset.removeColumn = header;
    remove.title = "Remove optional column";
    remove.setAttribute("aria-label", `Remove ${header}`);
    remove.textContent = "×";
    th.append(label, apply, remove);
    headRow.insertBefore(th, actionHeader);
    renderTable();
    rebuildColumnChoices(columnSearch.value);
  }

  function removeOptionalColumn(header) {
    optionalHeaders = optionalHeaders.filter(item => item !== header);
    rows.forEach(row => delete row.values[header]);
    const headerCell = Array.from(headRow.querySelectorAll("th[data-header]")).find(th => th.dataset.header === header);
    if (headerCell) headerCell.remove();
    renderTable();
    rebuildColumnChoices(columnSearch.value);
  }

  function rebuildColumnChoices(query) {
    const selected = columnSelect.value;
    const normalized = query.trim().toLowerCase();
    columnSelect.replaceChildren(new Option("Choose a column…", ""));
    const groups = new Map();
    optionalChoices.forEach(choice => {
      if (optionalHeaders.includes(choice.value)) return;
      if (normalized && !choice.label.toLowerCase().includes(normalized)) return;
      if (!groups.has(choice.group)) groups.set(choice.group, []);
      groups.get(choice.group).push(choice);
    });
    groups.forEach((choices, groupName) => {
      const group = document.createElement("optgroup");
      group.label = groupName;
      choices.forEach(choice => group.appendChild(new Option(choice.label, choice.value)));
      columnSelect.appendChild(group);
    });
    if (Array.from(columnSelect.options).some(option => option.value === selected)) {
      columnSelect.value = selected;
    }
  }

  function selectedDatabaseRows() {
    return databaseFiles.filter(item => selectedFileIds.has(String(item.file_id)));
  }

  function updateLoadButton() {
    const count = selectedFileIds.size;
    loadDatabaseButton.disabled = count === 0;
    loadDatabaseButton.textContent = count ? `Use ${count} selected file${count === 1 ? "" : "s"}` : "Use selected files";
  }

  function visibleDatabaseFiles() {
    return databaseFiles.filter(item =>
      (!experimentSelect.value || item.experiment_code === experimentSelect.value)
    );
  }

  function renderFilePicker() {
    filePicker.replaceChildren();
    const visible = visibleDatabaseFiles();
    if (!visible.length) {
      filePicker.className = "sdrf-file-picker sdrf-empty";
      if (experimentSelect.value) {
        filePicker.textContent = "This experiment has no acquired file records. You can use ‘From this computer’ for untracked files.";
      } else if (databaseFiles.length) {
        filePicker.textContent = "No files match these filters.";
      } else {
        filePicker.textContent = "This project has no acquired file records. Select an experiment to inspect it, or use ‘From this computer’.";
      }
    } else {
      filePicker.className = "sdrf-file-picker";
      visible.forEach(item => {
        const label = document.createElement("label");
        label.className = "sdrf-file-choice";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = item.file_id;
        checkbox.checked = selectedFileIds.has(String(item.file_id));
        checkbox.dataset.fileChoice = "";
        const description = document.createElement("span");
        const filename = document.createElement("strong");
        filename.textContent = item.row["comment[data file]"] || `File ${item.file_id}`;
        const context = document.createElement("small");
        context.textContent = `${item.experiment_code || "Unlinked"} / ${item.row["source name"]}`;
        description.append(filename, context);
        label.append(checkbox, description);
        filePicker.appendChild(label);
      });
    }
    selectAllButton.disabled = visible.length === 0;
    clearSelectionButton.disabled = selectedFileIds.size === 0;
    updateLoadButton();
  }

  function populateExperimentSelect(experiments) {
    const current = experimentSelect.value;
    experimentSelect.replaceChildren(new Option("All experiments", ""));
    experiments.forEach(experiment => {
      const count = experiment.file_count;
      const label = `${experiment.code} — ${experiment.name} (${count} file${count === 1 ? "" : "s"})`;
      experimentSelect.appendChild(new Option(label, experiment.code));
    });
    experimentSelect.disabled = experiments.length === 0;
    if (experiments.some(experiment => experiment.code === current)) {
      experimentSelect.value = current;
    }
  }

  async function loadProject(projectCode) {
    databaseFiles = [];
    selectedFileIds.clear();
    populateExperimentSelect([]);
    if (!projectCode) {
      filePicker.className = "sdrf-file-picker sdrf-empty";
      filePicker.textContent = "Select a project to see its acquired files.";
      renderFilePicker();
      return;
    }
    filePicker.className = "sdrf-file-picker sdrf-empty";
    filePicker.textContent = "Loading files…";
    try {
      const response = await fetch(`/api/sdrf/project-files?project=${encodeURIComponent(projectCode)}`);
      if (!response.ok) throw new Error(await response.text());
      const data = await response.json();
      databaseFiles = data.files || [];
      populateExperimentSelect(data.experiments || []);
      renderFilePicker();
    } catch (error) {
      filePicker.textContent = "The project files could not be loaded. Please try again.";
      setStatus("Could not load project files", "error");
    }
  }

  function loadLocalFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    rows = files.map(localDraft);
    optionalHeaders.forEach(header => rows.forEach(row => { row.values[header] = ""; }));
    showErrors([]);
    renderTable();
  }

  function validateRows() {
    const messages = [];
    table.querySelectorAll(".sdrf-invalid").forEach(cell => cell.classList.remove("sdrf-invalid"));
    if (!rows.length) return ["Load at least one file."];
    rows.forEach((row, rowIndex) => {
      requiredHeaders.forEach(header => {
        if (cleanCell(row.values[header])) return;
        messages.push(`Row ${rowIndex + 1}: ${header} is required.`);
        const tr = tableBody.querySelector(`tr[data-row-index="${rowIndex}"]`);
        const cell = Array.from(tr.querySelectorAll("td[data-header]")).find(td => td.dataset.header === header);
        if (cell) cell.classList.add("sdrf-invalid");
      });
    });
    return messages;
  }

  function tableTsv(headers = allHeaders()) {
    const lines = [headers.join("\t")];
    rows.forEach(row => lines.push(headers.map(header => (
      cleanCell(row.values[header] ?? automaticColumns[header] ?? "")
    )).join("\t")));
    return `${lines.join("\n")}\n`;
  }

  async function copyTable() {
    if (!rows.length) {
      showErrors(["Load at least one file."]);
      return;
    }
    normalizeAllControlledTerms();
    renderTable();
    try {
      await navigator.clipboard.writeText(tableTsv());
      setStatus("Table copied to clipboard");
    } catch (error) {
      const helper = document.createElement("textarea");
      helper.value = tableTsv();
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      document.execCommand("copy");
      helper.remove();
      setStatus("Table copied to clipboard");
    }
  }

  function exportTable() {
    normalizeAllControlledTerms();
    renderTable();
    const validationErrors = validateRows();
    showErrors(validationErrors);
    if (validationErrors.length) {
      setStatus(`${validationErrors.length} required value${validationErrors.length === 1 ? " is" : "s are"} missing`, "error");
      const firstInvalid = table.querySelector(".sdrf-invalid");
      if (firstInvalid) firstInvalid.focus();
      return;
    }
    const blob = new Blob([tableTsv(exportHeaders())], { type: "text/tab-separated-values;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const prefix = projectSelect.value || "metadata";
    link.href = url;
    link.download = `${prefix}.sdrf.tsv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setStatus(`Exported ${rows.length} SDRF row${rows.length === 1 ? "" : "s"}`);
  }

  function closeApplyDialog() {
    if (typeof applyDialog.close === "function") applyDialog.close();
    else applyDialog.removeAttribute("open");
  }

  function openApplyDialog(header) {
    if (!rows.length) {
      showErrors(["Load at least one file before applying a value."]);
      return;
    }
    activeApplyHeader = header;
    applyHeaderLabel.textContent = header;
    const existingValues = [...new Set(rows.map(row => cleanCell(row.values[header])))];
    applyValue.value = existingValues.length === 1 ? existingValues[0] : "";
    if (header === "comment[proteomics data acquisition method]") {
      applyHint.textContent = "Enter DDA, DIA, PRM, or SRM; it will be converted to the corresponding PRIDE term.";
    } else if (header === "comment[instrument]") {
      applyHint.textContent = "Enter an exact instrument model, such as Orbitrap Fusion Lumos or Q Exactive HF-X.";
    } else {
      applyHint.textContent = "This value will replace the current value only for the selected files.";
    }
    applyFiles.replaceChildren();
    rows.forEach((row, rowIndex) => {
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.dataset.applyRow = rowIndex;
      const name = row.values["comment[data file]"] || row.values["assay name"] || `Row ${rowIndex + 1}`;
      const text = document.createElement("span");
      text.textContent = name;
      label.append(checkbox, text);
      applyFiles.appendChild(label);
    });
    if (typeof applyDialog.showModal === "function") applyDialog.showModal();
    else applyDialog.setAttribute("open", "");
    applyValue.focus();
    applyValue.select();
  }

  function setAllApplyFiles(checked) {
    applyFiles.querySelectorAll("input[data-apply-row]").forEach(input => {
      input.checked = checked;
    });
  }

  function applyValueToSelectedFiles() {
    const selectedRows = Array.from(
      applyFiles.querySelectorAll("input[data-apply-row]:checked"),
      input => Number(input.dataset.applyRow)
    );
    if (!selectedRows.length) {
      applyHint.textContent = "Select at least one file.";
      return;
    }
    const value = normalizeControlledTerm(activeApplyHeader, applyValue.value);
    selectedRows.forEach(rowIndex => {
      rows[rowIndex].values[activeApplyHeader] = value;
    });
    closeApplyDialog();
    showErrors([]);
    renderTable();
    setStatus(`Applied ${activeApplyHeader} to ${selectedRows.length} file${selectedRows.length === 1 ? "" : "s"}`);
  }

  document.querySelectorAll(".sdrf-source-tab").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".sdrf-source-tab").forEach(tab => {
        const active = tab === button;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      document.querySelectorAll(".sdrf-source-panel").forEach(panel => {
        panel.hidden = panel.id !== `source-${button.dataset.source}`;
      });
    });
  });

  projectSelect.addEventListener("change", () => loadProject(projectSelect.value));
  experimentSelect.addEventListener("change", () => {
    renderFilePicker();
  });
  filePicker.addEventListener("change", event => {
    if (!event.target.matches("[data-file-choice]")) return;
    if (event.target.checked) selectedFileIds.add(event.target.value);
    else selectedFileIds.delete(event.target.value);
    clearSelectionButton.disabled = selectedFileIds.size === 0;
    updateLoadButton();
  });
  selectAllButton.addEventListener("click", () => {
    visibleDatabaseFiles().forEach(item => selectedFileIds.add(String(item.file_id)));
    renderFilePicker();
  });
  clearSelectionButton.addEventListener("click", () => {
    selectedFileIds.clear();
    renderFilePicker();
  });
  loadDatabaseButton.addEventListener("click", () => {
    rows = selectedDatabaseRows().map(item => ({
      key: `db-${item.file_id}`,
      optional: item.optional || {},
      values: { ...item.row }
    }));
    optionalHeaders.forEach(header => rows.forEach(row => {
      row.values[header] = row.optional[header] || "";
    }));
    showErrors([]);
    renderTable();
  });

  localFileInput.addEventListener("change", () => loadLocalFiles(localFileInput.files));
  ["dragenter", "dragover"].forEach(type => dropZone.addEventListener(type, event => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach(type => dropZone.addEventListener(type, event => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  }));
  dropZone.addEventListener("drop", event => loadLocalFiles(event.dataTransfer.files));

  tableBody.addEventListener("input", event => {
    if (event.target.matches("td[contenteditable]")) syncCell(event.target);
  });
  tableBody.addEventListener("blur", event => {
    if (!event.target.matches("td[contenteditable]")) return;
    normalizeCell(event.target);
  }, true);
  tableBody.addEventListener("click", event => {
    const button = event.target.closest("[data-remove-row]");
    if (!button) return;
    rows.splice(Number(button.dataset.removeRow), 1);
    showErrors([]);
    renderTable();
  });
  tableBody.addEventListener("keydown", event => {
    const cell = event.target.closest("td[contenteditable]");
    if (!cell || (event.key !== "Enter" && event.key !== "Tab")) return;
    event.preventDefault();
    syncCell(cell);
    const cells = Array.from(tableBody.querySelectorAll("td[contenteditable]"));
    let index = cells.indexOf(cell) + (event.shiftKey ? -1 : 1);
    index = Math.max(0, Math.min(cells.length - 1, index));
    cells[index].focus();
  });
  tableBody.addEventListener("paste", event => {
    const startCell = event.target.closest("td[contenteditable]");
    const text = event.clipboardData && event.clipboardData.getData("text/plain");
    if (!startCell || !text || (!text.includes("\t") && !text.includes("\n"))) return;
    event.preventDefault();
    const startRow = Number(startCell.closest("tr").dataset.rowIndex);
    const headers = allHeaders();
    const startColumn = headers.indexOf(startCell.dataset.header);
    const pastedRows = text.replace(/\r/g, "").replace(/\n$/, "").split("\n").map(line => line.split("\t"));
    pastedRows.forEach((values, rowOffset) => {
      const targetRow = rows[startRow + rowOffset];
      if (!targetRow) return;
      values.forEach((value, columnOffset) => {
        const header = headers[startColumn + columnOffset];
        if (header) targetRow.values[header] = cleanCell(value);
      });
    });
    showErrors([]);
    renderTable();
    const target = tableBody.querySelector(`tr[data-row-index="${startRow}"] td[data-header="${CSS.escape(headers[startColumn])}"]`);
    if (target) target.focus();
  });

  headRow.addEventListener("click", event => {
    const applyButton = event.target.closest("[data-apply-column]");
    if (applyButton) {
      openApplyDialog(applyButton.dataset.applyColumn);
      return;
    }
    const removeButton = event.target.closest("[data-remove-column]");
    if (removeButton) removeOptionalColumn(removeButton.dataset.removeColumn);
  });
  document.getElementById("sdrf-add-column-button").addEventListener("click", () => {
    const typedHeader = columnSearch.value.trim();
    const customFactor = /^factor value\[[^\[\]\t\r\n]+\]$/i.test(typedHeader) ? typedHeader.toLowerCase() : "";
    addOptionalColumn(columnSelect.value || customFactor);
    columnSelect.value = "";
    if (customFactor) columnSearch.value = "";
  });
  columnSelect.addEventListener("dblclick", () => {
    addOptionalColumn(columnSelect.value);
    columnSelect.value = "";
  });
  columnSearch.addEventListener("input", () => rebuildColumnChoices(columnSearch.value));
  document.getElementById("sdrf-copy-table").addEventListener("click", copyTable);
  document.getElementById("sdrf-export").addEventListener("click", exportTable);
  document.getElementById("sdrf-apply-close").addEventListener("click", closeApplyDialog);
  document.getElementById("sdrf-apply-cancel").addEventListener("click", closeApplyDialog);
  document.getElementById("sdrf-apply-select-all").addEventListener("click", () => setAllApplyFiles(true));
  document.getElementById("sdrf-apply-clear-all").addEventListener("click", () => setAllApplyFiles(false));
  document.getElementById("sdrf-apply-confirm").addEventListener("click", applyValueToSelectedFiles);
  applyDialog.addEventListener("click", event => {
    if (event.target === applyDialog) closeApplyDialog();
  });

  const initialProject = app.dataset.initialProject;
  if (initialProject && Array.from(projectSelect.options).some(option => option.value === initialProject)) {
    projectSelect.value = initialProject;
    loadProject(initialProject);
  }
})();
