"use strict";
// Modal filter popups generated from the GET /api/filters catalogue. Every label is set with
// textContent; option values travel JSON-encoded in value attributes.

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(name, value === true ? "" : String(value));
  }
  if (text != null) node.textContent = text;
  return node;
}
function sourceTag(sources) {
  return sources ? el("span", { class: "src-tag" }, sources) : null;
}
function controlFor(name, field) {
  if (field.options) {
    const select = el("select", { name });
    for (const o of field.options) {
      select.appendChild(el("option", { value: o.value === null ? "" : JSON.stringify(o.value) }, o.label));
    }
    return select;
  }
  return el("input", { type: "number", name, min: field.min, max: field.max, step: field.step ?? "any",
    placeholder: "Any" });
}
function renderField(field) {
  const box = el("div", { class: "field", "data-key": field.key });
  const head = el("div", { class: "field-head" });
  head.append(el("span", { class: "field-label" }, field.label));
  const tag = sourceTag(field.sources);
  if (tag) head.append(tag);
  box.append(head);
  if (field.type === "multi") {
    const chips = el("div", { class: "chips" });
    for (const o of field.options) {
      const label = el("label", { class: "chip" });
      label.append(el("input", { type: "checkbox", name: field.key, value: JSON.stringify(o.value) }),
        document.createTextNode(` ${o.label}`));
      const optionTag = sourceTag(o.sources);
      if (optionTag) label.append(optionTag);
      chips.append(label);
    }
    box.append(chips);
  } else if (field.type === "range") {
    const row = el("div", { class: "range-row" });
    row.append(controlFor(field.min_key, field), el("span", {}, "–"), controlFor(field.max_key, field));
    if (field.unit) row.append(el("span", { class: "unit" }, field.unit));
    box.append(row);
  } else if (field.type === "number" || field.type === "select") {
    box.append(controlFor(field.key, field));
  } else if (field.type === "bool") {
    const label = el("label", { class: "chip" });
    // disabled: e.g. loop_only without a Wikiloc login (catalogue sets field.disabled)
    label.append(el("input", { type: "checkbox", name: field.key, disabled: Boolean(field.disabled) }),
      document.createTextNode(" Yes"));
    box.append(label);
  }
  return box;
}
function readControl(control) {
  if (control.tagName === "SELECT") return control.value === "" ? null : JSON.parse(control.value);
  return control.value === "" ? null : Number(control.value);
}
function writeControl(control, value) {
  if (control.tagName === "SELECT") control.value = value === null ? "" : JSON.stringify(value);
  else control.value = value === null ? "" : String(value);
}

// shared: [{name, label, type: "date"|"checkbox"|"number"|"slider", min, max, step, format}]
// getShared() -> {name: value}; onApply(values, shared) stores them.
function createFilterDialog({ id, title, section, shared, getValues, getShared, onApply }) {
  const dialog = el("dialog", { id, class: "filters-dialog" });
  const form = el("form", { novalidate: true });
  form.append(el("h2", {}, title));
  const sharedBox = el("fieldset", { class: "shared" });
  sharedBox.append(el("legend", {}, "Search settings"));
  for (const s of shared) {
    const label = el("label", { class: "shared-item" }, `${s.label} `);
    const input = el("input", { type: s.type === "slider" ? "range" : s.type, name: `shared_${s.name}`,
      min: s.min, max: s.max, step: s.step });
    label.append(input);
    if (s.format) {
      const out = el("span", { class: "slider-text" });
      input.addEventListener("input", () => { out.textContent = s.format(Number(input.value)); });
      label.append(out);
    }
    sharedBox.append(label);
  }
  form.append(sharedBox);
  const fields = el("div", { class: "fields" });
  for (const field of section.fields) fields.append(renderField(field));
  form.append(fields);
  const error = el("p", { class: "form-error", role: "alert" });
  form.append(error);
  const buttons = el("div", { class: "dialog-buttons" });
  const reset = el("button", { type: "button", "data-act": "reset" }, "Reset to defaults");
  const cancel = el("button", { type: "button", "data-act": "cancel" }, "Cancel");
  const apply = el("button", { type: "button", "data-act": "apply", class: "primary" }, "Apply");
  buttons.append(reset, cancel, apply);
  form.append(buttons);
  dialog.append(form);
  document.body.append(dialog);

  const control = (name) => form.querySelector(`[name="${name}"]`);
  function fill(values) {
    for (const field of section.fields) {
      if (field.type === "multi") {
        const chosen = new Set(values[field.key].map((v) => JSON.stringify(v)));
        for (const box of form.querySelectorAll(`input[name="${field.key}"]`)) box.checked = chosen.has(box.value);
      } else if (field.type === "range") {
        writeControl(control(field.min_key), values[field.min_key]);
        writeControl(control(field.max_key), values[field.max_key]);
      } else if (field.type === "bool") {
        control(field.key).checked = Boolean(values[field.key]);
      } else {
        writeControl(control(field.key), values[field.key]);
      }
    }
  }
  function read() {
    const values = {};
    for (const field of section.fields) {
      if (field.type === "multi") {
        values[field.key] = [...form.querySelectorAll(`input[name="${field.key}"]:checked`)]
          .map((box) => JSON.parse(box.value));
      } else if (field.type === "range") {
        values[field.min_key] = readControl(control(field.min_key));
        values[field.max_key] = readControl(control(field.max_key));
      } else if (field.type === "bool") {
        values[field.key] = control(field.key).checked;
      } else {
        values[field.key] = readControl(control(field.key));
      }
    }
    return values;
  }
  function fillShared(values) {
    for (const s of shared) {
      const input = control(`shared_${s.name}`);
      if (s.type === "checkbox") input.checked = Boolean(values[s.name]);
      else input.value = String(values[s.name]);
      input.dispatchEvent(new Event("input"));
    }
  }
  function readShared() {
    const values = {};
    for (const s of shared) {
      const input = control(`shared_${s.name}`);
      values[s.name] = s.type === "checkbox" ? input.checked
        : s.type === "date" ? input.value : Number(input.value);
    }
    return values;
  }
  function validate() {
    for (const node of form.querySelectorAll(".invalid")) node.classList.remove("invalid");
    const problems = [];
    for (const input of form.querySelectorAll("input, select")) {
      if (!input.checkValidity()) {
        input.classList.add("invalid");
        problems.push("Check the highlighted values");
      }
    }
    const values = read();
    for (const key of invalidRanges(values, section)) {
      const field = section.fields.find((f) => f.key === key);
      control(field.min_key).classList.add("invalid");
      control(field.max_key).classList.add("invalid");
      problems.push(`${field.label}: minimum is above maximum`);
    }
    const start = control("shared_start"), end = control("shared_end");
    if (start && end && start.value && end.value && end.value < start.value) {
      end.classList.add("invalid");
      problems.push("End date is before start date");
    }
    error.textContent = [...new Set(problems)].join(" · ");
    apply.disabled = problems.length > 0;
    return problems.length === 0;
  }

  form.addEventListener("input", validate);
  form.addEventListener("change", validate);
  reset.addEventListener("click", () => { fill(cloneJson(section.defaults)); validate(); });
  cancel.addEventListener("click", () => dialog.close());
  apply.addEventListener("click", () => {
    if (!validate()) return;
    onApply(read(), readShared());
    dialog.close();
  });
  return {
    dialog,
    open() {
      fill(getValues());
      fillShared(getShared());
      validate();
      dialog.showModal();
    },
  };
}
