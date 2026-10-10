const SESSION_KEY = "productFormCaptureSessionV1";

const scenarioInput = document.getElementById("scenario");
const screenshotInput = document.getElementById("includeScreenshot");
const captureButton = document.getElementById("capture");
const exportButton = document.getElementById("export");
const clearButton = document.getElementById("clear");
const statusElement = document.getElementById("status");
const countElement = document.getElementById("captureCount");
const listElement = document.getElementById("captureList");
const fillFieldSelect = document.getElementById("fillField");
const fillValueInput = document.getElementById("fillValue");
const fillSuggestions = document.getElementById("fillSuggestions");
const fillHint = document.getElementById("fillHint");
const fillButton = document.getElementById("fillTest");
const fillStatusElement = document.getElementById("fillStatus");

let currentFillableFields = [];
let currentCaptureForFill = null;
let fillArmExpiresAt = 0;
let fillArmTimer = null;

const setStatus = (message, kind = "") => {
  statusElement.textContent = message;
  statusElement.className = `status ${kind}`.trim();
};

const setFillStatus = (message, kind = "") => {
  fillStatusElement.textContent = message;
  fillStatusElement.className = `status ${kind}`.trim();
};

const chromeCall = (fn, ...args) =>
  new Promise((resolve, reject) => {
    fn(...args, (result) => {
      const error = chrome.runtime.lastError;
      if (error) reject(new Error(error.message));
      else resolve(result);
    });
  });

const getActiveTab = async () => {
  const tabs = await chromeCall(chrome.tabs.query.bind(chrome.tabs), {
    active: true,
    currentWindow: true
  });
  const tab = tabs?.[0];
  if (!tab?.id) throw new Error("无法识别当前页面。");
  if (!/^https?:/i.test(tab.url || "")) {
    throw new Error("请在 HTTP/HTTPS 业务页面上运行，浏览器设置页无法采集。");
  }
  return tab;
};

const injectCollector = async (tabId) => {
  return chromeCall(chrome.scripting.executeScript.bind(chrome.scripting), {
    target: { tabId, allFrames: true },
    files: ["content.js"]
  });
};

const executeAcrossFrames = async (tabId, func, args = []) =>
  chromeCall(chrome.scripting.executeScript.bind(chrome.scripting), {
    target: { tabId, allFrames: true },
    func,
    args
  });

const executeInFrame = async (tabId, frameId, func, args = [], world = "ISOLATED") =>
  chromeCall(chrome.scripting.executeScript.bind(chrome.scripting), {
    target: { tabId, frameIds: [frameId] },
    func,
    args,
    world
  });

async function fillInMainWorld(descriptor) {
  const FIELD_SELECTOR = [
    "input:not([type='hidden']):not([type='password'])",
    "select",
    "textarea",
    "[contenteditable='true']",
    "[role='textbox']",
    "[role='combobox']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='switch']"
  ].join(",");
  const OPTION_SELECTOR = [
    "[role='option']",
    "[role='menuitemradio']",
    "[role='menuitemcheckbox']",
    ".ivu-select-item",
    ".ivu-cascader-menu-item",
    ".ivu-auto-complete li",
    ".el-select-dropdown__item",
    ".el-cascader-node",
    ".el-autocomplete-suggestion li",
    ".ant-select-item-option-content",
    ".ant-cascader-menu-item-content",
    ".ant-dropdown-menu-item"
  ].join(",");
  const clean = (value, limit = 300) => {
    const text = String(value ?? "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    return text.length > limit ? text.slice(0, limit) : text;
  };
  const identity = (element, index) =>
    clean(
      element.getAttribute("name") ||
        element.id ||
        element.getAttribute("data-field") ||
        element.getAttribute("aria-label"),
      120
    ) || `field-${index + 1}`;
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const click = (element) => {
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    element.click();
  };

  const fields = Array.from(document.querySelectorAll(FIELD_SELECTOR));
  let element = fields.find((candidate, index) => identity(candidate, index) === descriptor.fieldKey);
  if (!element && Number.isInteger(descriptor.fieldIndex) && descriptor.fieldIndex > 0) {
    element = fields[descriptor.fieldIndex - 1];
  }
  if (!element) throw new Error("未找到目标字段。请刷新页面并重新采集后再试。");
  if (element.matches(":disabled") || element.getAttribute("aria-disabled") === "true") {
    throw new Error("目标字段当前为禁用状态。");
  }
  if (element.readOnly || element.getAttribute("aria-readonly") === "true") {
    throw new Error("目标字段当前为只读状态。");
  }

  const inputType = (element.getAttribute("type") || element.getAttribute("role") || "").toLowerCase();
  if (["password", "file", "hidden", "submit", "button"].includes(inputType)) {
    throw new Error("该控件类型不允许回填。");
  }

  if (["radio", "checkbox", "switch"].includes(inputType) || element.matches("[role='radio'], [role='checkbox'], [role='switch']")) {
    click(element);
    await wait(120);
    const selected =
      element.checked === true ||
      element.getAttribute("aria-checked") === "true" ||
      /checked|selected|active/.test(element.className || "") ||
      element.querySelector?.("input:checked");
    return { ok: true, method: "main-world-control-click", retained: Boolean(selected) };
  }

  if (element instanceof HTMLSelectElement) {
    const requested = clean(descriptor.requestedValue);
    const option = Array.from(element.options).find(
      (item) => clean(item.textContent) === requested || clean(item.value) === requested
    );
    if (!option) throw new Error(`没有找到选项“${requested}”。`);
    element.value = option.value;
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    await wait(120);
    return { ok: true, method: "main-world-native-select", retained: element.value === option.value };
  }

  const customSelect = element.closest(
    ".ivu-select, .ivu-cascader, .el-select, .el-cascader, .ant-select, .ant-cascader"
  );
  if (customSelect || element.getAttribute("role") === "combobox") {
    const requested = clean(descriptor.requestedValue);
    if (!requested) throw new Error("请输入完整的下拉选项文字。");
    const trigger =
      customSelect?.querySelector(
        ".ivu-select-selection, .ivu-cascader-rel, .el-input, .el-select__wrapper, .ant-select-selector, [role='combobox']"
      ) || customSelect || element;
    click(trigger);
    let options = [];
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await wait(120);
      options = Array.from(document.querySelectorAll(OPTION_SELECTOR)).filter(visible);
      if (options.length) break;
    }
    const exact = options.find((node) => clean(node.textContent) === requested);
    if (!exact) throw new Error(`下拉框中没有找到完全匹配的选项“${requested}”。`);
    click(exact);
    await wait(160);
    const retained = clean(customSelect?.textContent || trigger.textContent).includes(requested);
    return { ok: true, method: "main-world-custom-select", retained };
  }

  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    const requested = String(descriptor.requestedValue || "");
    element.focus();
    if (typeof element.select === "function") element.select();
    let insertedByCommand = false;
    try {
      insertedByCommand = document.execCommand("insertText", false, requested);
    } catch {
      insertedByCommand = false;
    }
    if (!insertedByCommand || element.value !== requested) {
      element.dispatchEvent(
        new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: requested })
      );
      const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (!setter) throw new Error("当前控件没有可用的文本赋值方式。");
      setter.call(element, requested);
      element.dispatchEvent(
        new InputEvent("input", { bubbles: true, cancelable: false, inputType: "insertText", data: requested })
      );
    }
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "Unidentified" }));
    element.blur();
    await wait(260);
    if (element.value !== requested) {
      throw new Error("已执行文本输入事件，但系统前端随后重置了字段值。");
    }
    return { ok: true, method: "main-world-text-input", retained: true };
  }

  throw new Error("该字段暂不支持回填。");
}

const uniqueStrings = (items) => Array.from(new Set(items.filter(Boolean)));

const normalizeObservedOptionSets = (sets = []) => {
  const normalized = [];
  for (const item of sets) {
    const options = uniqueStrings(item?.options || []);
    if (!options.length) continue;
    const signature = `${item.frameId ?? ""}\u0000${options.join("\u0001")}`;
    const sameIndexes = normalized
      .map((candidate, index) => ({ candidate, index }))
      .filter(({ candidate }) => candidate.__signature === signature);

    if (item.fieldKey) {
      const sameField = sameIndexes.find(({ candidate }) => candidate.fieldKey === item.fieldKey);
      if (sameField) continue;
      for (let index = normalized.length - 1; index >= 0; index -= 1) {
        if (normalized[index].__signature === signature && !normalized[index].fieldKey) {
          normalized.splice(index, 1);
        }
      }
    } else if (sameIndexes.length) {
      continue;
    }

    normalized.push({ ...item, options, __signature: signature });
  }
  return normalized.map(({ __signature, ...item }) => item);
};

const normalizeCaptureData = (capture = {}) => {
  const observedOptionSets = normalizeObservedOptionSets(capture.observedOptionSets || []);
  const fields = (capture.fields || []).map((field) => {
    const observedOptions = uniqueStrings(
      observedOptionSets
        .filter((item) => item.frameId === field.frameId && item.fieldKey === field.fieldKey)
        .flatMap((item) => item.options || [])
    );
    return {
      ...field,
      options: uniqueStrings([...(field.options || []), ...observedOptions])
    };
  });
  return { ...capture, schemaVersion: 4, fields, observedOptionSets };
};

const mergeFrameCaptures = (injectionResults, scenarioLabel) => {
  const frames = injectionResults
    .filter((entry) => entry?.result?.fields)
    .map((entry) => ({
      frameId: entry.frameId,
      documentId: entry.documentId || null,
      data: entry.result
    }));

  if (!frames.length) throw new Error("未能从顶层页面或内嵌页面中读取到表单结构。");

  const topFrame = frames.find((frame) => frame.frameId === 0) || frames[0];
  const fields = frames.flatMap((frame) =>
    frame.data.fields.map((field) => ({
      ...field,
      frameId: frame.frameId,
      framePath: frame.data.page.path
    }))
  );

  return normalizeCaptureData({
    schemaVersion: 4,
    capturedAt: new Date().toISOString(),
    scenarioLabel,
    page: {
      ...topFrame.data.page,
      capturedFrameCount: frames.length
    },
    frameSummaries: frames.map((frame) => ({
      frameId: frame.frameId,
      isTopFrame: frame.frameId === 0,
      path: frame.data.page.path,
      fieldCount: frame.data.fields.length,
      actionButtonCount: frame.data.actionButtons.length
    })),
    fields,
    openOptions: uniqueStrings(frames.flatMap((frame) => frame.data.openOptions)),
    observedOptionSets: frames.flatMap((frame) =>
      (frame.data.observedOptionSets || []).map((item) => ({
        ...item,
        frameId: frame.frameId,
        framePath: frame.data.page.path
      }))
    ),
    pageMessages: uniqueStrings(frames.flatMap((frame) => frame.data.pageMessages)),
    actionButtons: uniqueStrings(frames.flatMap((frame) => frame.data.actionButtons)),
    privacy: {
      fieldValuesCollected: false,
      cookiesCollected: false,
      localStorageCollected: false,
      networkTrafficCollected: false
    }
  });
};

const loadSession = async () => {
  const stored = await chromeCall(chrome.storage.local.get.bind(chrome.storage.local), SESSION_KEY);
  const existing = stored?.[SESSION_KEY];
  if (existing) return { ...existing, schemaVersion: 4 };
  return {
    schemaVersion: 4,
    createdAt: new Date().toISOString(),
    captures: []
  };
};

const saveSession = async (session) => {
  await chromeCall(chrome.storage.local.set.bind(chrome.storage.local), { [SESSION_KEY]: session });
};

const fieldDisplayName = (field) => {
  const group = String(field.groupLabel || "").trim();
  const label = String(field.label || "").trim();
  if (group && label && group !== label) return `${group} / ${label}`;
  return group || label || field.placeholder || field.fieldKey;
};

const clickOnlyField = (field) =>
  ["radio", "checkbox", "switch"].includes(String(field.inputType || "").toLowerCase());

const resetFillArm = () => {
  fillArmExpiresAt = 0;
  if (fillArmTimer) clearTimeout(fillArmTimer);
  fillArmTimer = null;
  fillButton.textContent = "仅回填该字段";
};

const observedOptionsForField = (field) => {
  const sets = currentCaptureForFill?.observedOptionSets || [];
  const exact = sets.filter(
    (item) =>
      item.frameId === field.frameId &&
      (item.fieldKey === field.fieldKey ||
        (item.label && [field.groupLabel, field.label].filter(Boolean).includes(item.label)))
  );
  return uniqueStrings([...field.options, ...exact.flatMap((item) => item.options || [])]);
};

const updateFillControl = () => {
  resetFillArm();
  const field =
    fillFieldSelect.value === "" ? null : currentFillableFields[Number(fillFieldSelect.value)];
  fillSuggestions.replaceChildren();
  setFillStatus("");
  if (!field) {
    fillValueInput.disabled = true;
    fillButton.disabled = true;
    return;
  }

  const isClickOnly = clickOnlyField(field);
  const suggestions = observedOptionsForField(field);
  for (const value of suggestions) {
    const option = document.createElement("option");
    option.value = value;
    fillSuggestions.appendChild(option);
  }

  fillValueInput.value = "";
  fillValueInput.disabled = isClickOnly;
  fillButton.disabled = false;
  if (isClickOnly) {
    fillValueInput.placeholder = "该选项将被直接点击";
    fillHint.textContent = `将点击“${fieldDisplayName(field)}”，不需要输入测试内容。`;
  } else if (suggestions.length) {
    fillValueInput.placeholder = "输入或选择完整的下拉选项文字";
    fillHint.textContent = `已记录 ${suggestions.length} 个可选值，回填时会打开下拉框并点击完全匹配项。`;
  } else {
    fillValueInput.placeholder = "输入无敏感的测试内容";
    fillHint.textContent = "该字段将按普通文本框回填，并触发 input、change 和 blur 事件。";
  }
};

const renderFillFields = (session) => {
  const captures = session?.captures || [];
  currentCaptureForFill = captures.length ? normalizeCaptureData(captures[captures.length - 1].data) : null;
  const allCandidates = (currentCaptureForFill?.fields || []).filter(
    (field) =>
      field.visible &&
      !field.disabled &&
      !field.readOnly &&
      !["password", "file", "hidden", "submit", "button"].includes(
        String(field.inputType || "").toLowerCase()
      )
  );
  const businessCandidates = allCandidates.filter((field) => field.frameId !== 0);
  currentFillableFields = businessCandidates.length ? businessCandidates : allCandidates;

  fillFieldSelect.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = currentFillableFields.length ? "选择一个字段" : "请先采集当前页";
  fillFieldSelect.appendChild(empty);

  currentFillableFields.forEach((field, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = fieldDisplayName(field);
    fillFieldSelect.appendChild(option);
  });
  fillFieldSelect.disabled = currentFillableFields.length === 0;
  fillValueInput.disabled = true;
  fillButton.disabled = true;
  fillSuggestions.replaceChildren();
};

const renderSession = (session) => {
  const captures = session?.captures || [];
  countElement.textContent = `${captures.length} 个状态`;
  exportButton.disabled = captures.length === 0;
  listElement.replaceChildren();

  captures.forEach((capture, index) => {
    const item = document.createElement("li");
    const fieldCount = capture.data?.fields?.length || 0;
    const optionSetCount = normalizeCaptureData(capture.data).observedOptionSets.length;
    item.textContent = `${index + 1}. ${capture.data?.scenarioLabel || "未命名状态"}（${fieldCount} 个字段，${optionSetCount} 组下拉选项）`;
    listElement.appendChild(item);
  });
  renderFillFields(session);
};

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const captureScreenshot = async (tab) => {
  await executeAcrossFrames(tab.id, () => globalThis.__PRODUCT_FORM_CAPTURE_MASK__?.());
  try {
    await wait(160);
    return await chromeCall(chrome.tabs.captureVisibleTab.bind(chrome.tabs), tab.windowId, {
      format: "png"
    });
  } finally {
    await executeAcrossFrames(tab.id, () => globalThis.__PRODUCT_FORM_CAPTURE_UNMASK__?.()).catch(() => {});
  }
};

const captureCurrentState = async () => {
  captureButton.disabled = true;
  setStatus("正在读取当前页面结构…");

  try {
    const tab = await getActiveTab();
    await injectCollector(tab.id);

    const session = await loadSession();
    const scenarioLabel = scenarioInput.value.trim() || `状态 ${session.captures.length + 1}`;
    const frameResults = await executeAcrossFrames(
      tab.id,
      (label) => globalThis.__PRODUCT_FORM_CAPTURE_EXTRACT__?.(label) || null,
      [scenarioLabel]
    );
    const capture = mergeFrameCaptures(frameResults, scenarioLabel);

    const entry = {
      data: capture,
      screenshotDataUrl: null
    };

    if (screenshotInput.checked) {
      setStatus("正在遮住输入控件并保存当前可见区…");
      entry.screenshotDataUrl = await captureScreenshot(tab);
    }

    session.captures.push(entry);
    session.updatedAt = new Date().toISOString();

    try {
      await saveSession(session);
    } catch (error) {
      if (entry.screenshotDataUrl) {
        entry.screenshotDataUrl = null;
        await saveSession(session);
        throw new Error("截图体积超过浏览器本地存储限制，本次已改为只保存字段结构。");
      }
      throw error;
    }

    renderSession(session);
    scenarioInput.value = "";
    setStatus(
      `已采集“${scenarioLabel}”：${capture.page.capturedFrameCount} 个页面层、${capture.fields.length} 个可见字段、${capture.observedOptionSets.length} 组下拉选项。`,
      "success"
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    setStatus(message, "error");
  } finally {
    captureButton.disabled = false;
  }
};

const runFillTest = async () => {
  const field =
    fillFieldSelect.value === "" ? null : currentFillableFields[Number(fillFieldSelect.value)];
  if (!field) return;
  const clickOnly = clickOnlyField(field);
  const requestedValue = clickOnly ? "" : fillValueInput.value.trim();
  if (!clickOnly && !requestedValue) {
    setFillStatus("请输入测试内容或完整的下拉选项文字。", "error");
    return;
  }
  if (Date.now() > fillArmExpiresAt) {
    fillArmExpiresAt = Date.now() + 8000;
    fillButton.textContent = "再次点击确认回填";
    setFillStatus(`请在 8 秒内再次点击，确认只回填“${fieldDisplayName(field)}”。`);
    fillArmTimer = setTimeout(() => {
      resetFillArm();
      setFillStatus("回填确认已超时，未执行任何页面操作。");
    }, 8000);
    return;
  }
  resetFillArm();

  fillButton.disabled = true;
  setFillStatus("正在定位字段并执行回填…");
  try {
    const tab = await getActiveTab();
    await injectCollector(tab.id);
    const results = await executeInFrame(
      tab.id,
      field.frameId,
      fillInMainWorld,
      [{ fieldKey: field.fieldKey, fieldIndex: field.index, requestedValue }],
      "MAIN"
    );
    const result = results?.[0]?.result;
    if (!result?.ok) throw new Error("回填脚本未返回成功状态。");
    if (result.retained === false) {
      throw new Error("页面已收到操作，但没有检测到稳定的选中或保留状态。");
    }
    setFillStatus("系统字段已保留回填结果。请再目视确认联动状态，不要提交测试数据。", "success");
  } catch (error) {
    setFillStatus(error instanceof Error ? error.message : String(error), "error");
  } finally {
    fillButton.disabled = false;
  }
};

const crcTable = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

const crc32 = (bytes) => {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
};

const write16 = (view, offset, value) => view.setUint16(offset, value, true);
const write32 = (view, offset, value) => view.setUint32(offset, value >>> 0, true);

const concatBytes = (parts) => {
  const length = parts.reduce((total, part) => total + part.length, 0);
  const result = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
};

const dosDateTime = (date = new Date()) => {
  const year = Math.max(1980, date.getFullYear());
  const time = (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2);
  const day = (year - 1980) << 9 | (date.getMonth() + 1) << 5 | date.getDate();
  return { time, day };
};

const createZip = (files) => {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  const { time, day } = dosDateTime();
  let localOffset = 0;

  for (const file of files) {
    const nameBytes = encoder.encode(file.name);
    const data = file.data;
    const checksum = crc32(data);

    const localHeader = new Uint8Array(30);
    const localView = new DataView(localHeader.buffer);
    write32(localView, 0, 0x04034b50);
    write16(localView, 4, 20);
    write16(localView, 6, 0x0800);
    write16(localView, 8, 0);
    write16(localView, 10, time);
    write16(localView, 12, day);
    write32(localView, 14, checksum);
    write32(localView, 18, data.length);
    write32(localView, 22, data.length);
    write16(localView, 26, nameBytes.length);
    write16(localView, 28, 0);
    localParts.push(localHeader, nameBytes, data);

    const centralHeader = new Uint8Array(46);
    const centralView = new DataView(centralHeader.buffer);
    write32(centralView, 0, 0x02014b50);
    write16(centralView, 4, 20);
    write16(centralView, 6, 20);
    write16(centralView, 8, 0x0800);
    write16(centralView, 10, 0);
    write16(centralView, 12, time);
    write16(centralView, 14, day);
    write32(centralView, 16, checksum);
    write32(centralView, 20, data.length);
    write32(centralView, 24, data.length);
    write16(centralView, 28, nameBytes.length);
    write16(centralView, 30, 0);
    write16(centralView, 32, 0);
    write16(centralView, 34, 0);
    write16(centralView, 36, 0);
    write32(centralView, 38, 0);
    write32(centralView, 42, localOffset);
    centralParts.push(centralHeader, nameBytes);

    localOffset += localHeader.length + nameBytes.length + data.length;
  }

  const localData = concatBytes(localParts);
  const centralData = concatBytes(centralParts);
  const end = new Uint8Array(22);
  const endView = new DataView(end.buffer);
  write32(endView, 0, 0x06054b50);
  write16(endView, 4, 0);
  write16(endView, 6, 0);
  write16(endView, 8, files.length);
  write16(endView, 10, files.length);
  write32(endView, 12, centralData.length);
  write32(endView, 16, localData.length);
  write16(endView, 20, 0);

  return concatBytes([localData, centralData, end]);
};

const bytesFromDataUrl = (dataUrl) => {
  const base64 = dataUrl.split(",", 2)[1] || "";
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
};

const safeFilePart = (value, fallback) => {
  const cleaned = String(value || "")
    .trim()
    .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "-")
    .replace(/\s+/g, "-")
    .slice(0, 60);
  return cleaned || fallback;
};

const formatTimestamp = (date = new Date()) => {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(
    date.getMinutes()
  )}${pad(date.getSeconds())}`;
};

const exportSession = async () => {
  exportButton.disabled = true;
  setStatus("正在生成本地 ZIP…");
  try {
    const session = await loadSession();
    if (!session.captures.length) throw new Error("当前没有可导出的采集状态。");

    const encoder = new TextEncoder();
    const exportData = {
      schemaVersion: session.schemaVersion,
      createdAt: session.createdAt,
      updatedAt: session.updatedAt,
      captureCount: session.captures.length,
      captures: session.captures.map((entry, index) => ({
        ...normalizeCaptureData(entry.data),
        screenshotFile: entry.screenshotDataUrl
          ? `screenshots/${String(index + 1).padStart(2, "0")}-${safeFilePart(
              entry.data.scenarioLabel,
              `state-${index + 1}`
            )}.png`
          : null
      }))
    };

    const files = [
      {
        name: "capture-session.json",
        data: encoder.encode(JSON.stringify(exportData, null, 2))
      },
      {
        name: "README.txt",
        data: encoder.encode(
          [
            "产品编码表单规则采集包",
            "",
            `采集状态数：${exportData.captureCount}`,
            "主数据文件：capture-session.json",
            "截图目录：screenshots/（如果采集时启用了截图）",
            "",
            "采集器不读取字段当前值、Cookie、Token、localStorage 或网络请求。",
            "截图可能仍包含页面上的其他信息，请在离开内网前人工检查。"
          ].join("\n")
        )
      }
    ];

    session.captures.forEach((entry, index) => {
      if (!entry.screenshotDataUrl) return;
      files.push({
        name: exportData.captures[index].screenshotFile,
        data: bytesFromDataUrl(entry.screenshotDataUrl)
      });
    });

    const zipBytes = createZip(files);
    const blob = new Blob([zipBytes], { type: "application/zip" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `product-form-capture-${formatTimestamp()}.zip`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    setStatus("已生成 ZIP。请先在内网环境中解压检查，再决定是否带出。", "success");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), "error");
  } finally {
    const session = await loadSession().catch(() => ({ captures: [] }));
    exportButton.disabled = !session.captures.length;
  }
};

const clearSession = async () => {
  const session = await loadSession();
  if (!session.captures.length) return;
  if (!confirm("确定清空本次未导出的采集状态吗？")) return;
  await chromeCall(chrome.storage.local.remove.bind(chrome.storage.local), SESSION_KEY);
  renderSession({ captures: [] });
  setStatus("已清空本次采集。");
};

captureButton.addEventListener("click", captureCurrentState);
exportButton.addEventListener("click", exportSession);
clearButton.addEventListener("click", clearSession);
fillFieldSelect.addEventListener("change", updateFillControl);
fillValueInput.addEventListener("input", resetFillArm);
fillButton.addEventListener("click", runFillTest);

loadSession()
  .then(renderSession)
  .catch((error) => setStatus(error instanceof Error ? error.message : String(error), "error"));
