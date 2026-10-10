(() => {
  if (globalThis.__PRODUCT_FORM_CAPTURE_INSTALLED__) return;
  globalThis.__PRODUCT_FORM_CAPTURE_INSTALLED__ = true;

  const MAX_TEXT = 240;
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

  const cleanText = (value, limit = MAX_TEXT) => {
    const text = String(value ?? "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  };

  const isVisible = (element) => {
    if (!(element instanceof Element)) return false;
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const uniqueTexts = (items, limit = 100) => {
    const result = [];
    const seen = new Set();
    for (const item of items) {
      const text = cleanText(item);
      if (!text || seen.has(text)) continue;
      seen.add(text);
      result.push(text);
      if (result.length >= limit) break;
    }
    return result;
  };

  const textFromIdRefs = (value) => {
    if (!value) return "";
    return cleanText(
      value
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent || "")
        .join(" ")
    );
  };

  const nearbyContainer = (element) => {
    const preferred = element.closest(
      ".ant-form-item, .el-form-item, .ivu-form-item, .form-item, .form-group, .field, [class*='formItem'], [data-field]"
    );
    if (preferred) return preferred;

    let current = element.parentElement;
    for (let depth = 0; current && depth < 4; depth += 1) {
      if (current.querySelector("label, legend, [class*='label'], [data-label]")) return current;
      current = current.parentElement;
    }
    return element.parentElement;
  };

  const findGroupLabel = (element) => {
    const container = nearbyContainer(element);
    if (!container) return "";
    const candidate = container.querySelector(
      ".ant-form-item-label, .el-form-item__label, .ivu-form-item-label, .form-label, [class*='form-item-label'], [class*='field-label'], [data-label], legend"
    );
    return cleanText(candidate?.textContent || candidate?.getAttribute("data-label"), 160);
  };

  const findLabel = (element) => {
    const ariaLabel = cleanText(element.getAttribute("aria-label"));
    if (ariaLabel) return ariaLabel;

    const ariaLabelledBy = textFromIdRefs(element.getAttribute("aria-labelledby"));
    if (ariaLabelledBy) return ariaLabelledBy;

    if (element.labels?.length) {
      const explicit = cleanText(Array.from(element.labels).map((label) => label.textContent).join(" "));
      if (explicit) return explicit;
    }

    const wrappingLabel = element.closest("label");
    if (wrappingLabel) {
      const wrapped = cleanText(wrappingLabel.textContent);
      if (wrapped) return wrapped;
    }

    const container = nearbyContainer(element);
    const candidate = container?.querySelector(
      ":scope > label, :scope > legend, .ant-form-item-label, .el-form-item__label, .ivu-form-item-label, .form-label, [class*='form-item-label'], [class*='field-label'], [data-label]"
    );
    const nearby = cleanText(candidate?.textContent || candidate?.getAttribute("data-label"));
    if (nearby) return nearby;

    const previous = element.previousElementSibling;
    if (previous && /^(LABEL|LEGEND|SPAN|DIV)$/.test(previous.tagName)) {
      const previousText = cleanText(previous.textContent, 120);
      if (previousText) return previousText;
    }

    return cleanText(element.getAttribute("placeholder"), 120);
  };

  const findHelpText = (element) => {
    const container = nearbyContainer(element);
    if (!container) return [];
    const nodes = container.querySelectorAll(
      ".ant-form-item-extra, .ant-form-item-explain, .el-form-item__error, .ivu-form-item-error-tip, .form-text, .help-block, [class*='help'], [class*='explain'], [role='alert']"
    );
    return uniqueTexts(
      Array.from(nodes)
        .filter(isVisible)
        .map((node) => node.textContent),
      8
    );
  };

  const findValidationMessages = (element) => {
    const messages = [];
    if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) {
      if (!element.validity.valid && element.validationMessage) messages.push(element.validationMessage);
    }
    const container = nearbyContainer(element);
    if (container) {
      const nodes = container.querySelectorAll(
        "[role='alert'], .ant-form-item-explain-error, .el-form-item__error, .ivu-form-item-error-tip, .error, [class*='error-message'], [class*='errorMessage']"
      );
      for (const node of nodes) {
        if (isVisible(node)) messages.push(node.textContent);
      }
    }
    return uniqueTexts(messages, 8);
  };

  const findOptions = (element) => {
    if (element instanceof HTMLSelectElement) {
      return uniqueTexts(Array.from(element.options).map((option) => option.textContent), 200);
    }

    if (element instanceof HTMLInputElement && element.list) {
      return uniqueTexts(Array.from(element.list.options).map((option) => option.label || option.textContent), 200);
    }

    const controlId = element.getAttribute("aria-controls") || element.getAttribute("aria-owns");
    if (controlId) {
      const optionsContainer = document.getElementById(controlId);
      if (optionsContainer) {
        return uniqueTexts(
          Array.from(optionsContainer.querySelectorAll(`${OPTION_SELECTOR}, option`))
            .filter(isVisible)
            .map((node) => node.textContent),
          200
        );
      }
    }

    if (element === document.activeElement || element.getAttribute("aria-expanded") === "true") {
      const openOptions = uniqueTexts(
        Array.from(document.querySelectorAll(OPTION_SELECTOR))
          .filter(isVisible)
          .map((node) => node.textContent),
        300
      );
      if (openOptions.length) return openOptions;
    }

    const type = (element.getAttribute("type") || "").toLowerCase();
    const name = element.getAttribute("name");
    if ((type === "radio" || element.getAttribute("role") === "radio") && name) {
      const escapedName = CSS.escape(name);
      const group = Array.from(document.querySelectorAll(`[name="${escapedName}"]`));
      return uniqueTexts(group.map(findLabel), 100);
    }

    return [];
  };

  const fieldRequired = (element, label, container) => {
    if (element.hasAttribute("required") || element.getAttribute("aria-required") === "true") return true;
    if (/^\s*\*/.test(label)) return true;
    if (container?.matches(".required, [class*='required'], [aria-required='true']")) return true;
    if (container?.querySelector(".required, [class*='required'], [aria-required='true']")) return true;
    return false;
  };

  const fieldIdentity = (element, index) => {
    const raw =
      element.getAttribute("name") ||
      element.id ||
      element.getAttribute("data-field") ||
      element.getAttribute("aria-label");
    return cleanText(raw, 120) || `field-${index + 1}`;
  };

  const describeField = (element, index) => {
    const label = findLabel(element);
    const container = nearbyContainer(element);
    const role = cleanText(element.getAttribute("role"), 40);
    const inputType =
      element instanceof HTMLInputElement
        ? cleanText(element.type || "text", 40)
        : role || cleanText(element.tagName.toLowerCase(), 40);

    return {
      index: index + 1,
      fieldKey: fieldIdentity(element, index),
      label,
      groupLabel: findGroupLabel(element),
      element: element.tagName.toLowerCase(),
      inputType,
      required: fieldRequired(element, label, container),
      disabled: element.matches(":disabled") || element.getAttribute("aria-disabled") === "true",
      readOnly: Boolean(element.readOnly) || element.getAttribute("aria-readonly") === "true",
      visible: isVisible(element),
      placeholder: cleanText(element.getAttribute("placeholder"), 160),
      options: findOptions(element),
      helpText: findHelpText(element),
      validationMessages: findValidationMessages(element)
    };
  };

  const observedOptionSets = [];
  let lastFieldContext = null;

  const fieldContextFromNode = (node) => {
    if (!(node instanceof Element)) return null;
    const direct = node.matches(FIELD_SELECTOR) ? node : node.closest(FIELD_SELECTOR);
    const customWrapper = node.closest(
      ".ivu-select, .ivu-cascader, .el-select, .el-cascader, .ant-select, .ant-cascader"
    );
    const element = direct || customWrapper?.querySelector(FIELD_SELECTOR);
    if (!element) return null;
    const allFields = Array.from(document.querySelectorAll(FIELD_SELECTOR));
    const index = allFields.indexOf(element);
    return index >= 0 ? describeField(element, index) : null;
  };

  const rememberFieldContext = (node) => {
    const context = fieldContextFromNode(node);
    if (context) lastFieldContext = context;
  };

  const optionContainer = (node) =>
    node.closest(
      ".ivu-select-dropdown, .ivu-cascader-transfer, .el-select-dropdown, .el-cascader__dropdown, .el-autocomplete-suggestion, .ant-select-dropdown, .ant-cascader-dropdown, [role='listbox'], [role='menu']"
    ) || node.parentElement;

  const recordVisibleOptionSets = () => {
    const visibleNodes = Array.from(document.querySelectorAll(OPTION_SELECTOR)).filter(isVisible);
    if (!visibleNodes.length) return;

    const groups = new Map();
    for (const node of visibleNodes) {
      const container = optionContainer(node);
      if (!container) continue;
      if (!groups.has(container)) groups.set(container, []);
      groups.get(container).push(node.textContent);
    }

    const allFields = Array.from(document.querySelectorAll(FIELD_SELECTOR));
    const active = document.activeElement;
    const activeIndex = allFields.indexOf(active);
    const activeField = activeIndex >= 0 ? describeField(active, activeIndex) : lastFieldContext;

    for (const texts of groups.values()) {
      const options = uniqueTexts(texts, 300);
      if (!options.length) continue;
      const sameOptions = (item) => item.options.join("\u0001") === options.join("\u0001");
      const sameField = observedOptionSets.find(
        (item) => sameOptions(item) && item.fieldKey === (activeField?.fieldKey || null)
      );
      if (sameField) continue;

      const unassignedIndex = observedOptionSets.findIndex(
        (item) => sameOptions(item) && !item.fieldKey
      );
      if (activeField?.fieldKey && unassignedIndex >= 0) {
        observedOptionSets[unassignedIndex] = {
          observedAt: new Date().toISOString(),
          fieldKey: activeField.fieldKey,
          label: activeField.groupLabel || activeField.label || null,
          options
        };
        continue;
      }
      if (!activeField?.fieldKey && observedOptionSets.some(sameOptions)) continue;

      observedOptionSets.push({
        observedAt: new Date().toISOString(),
        fieldKey: activeField?.fieldKey || null,
        label: activeField?.groupLabel || activeField?.label || null,
        options
      });
      if (observedOptionSets.length > 100) observedOptionSets.shift();
    }
  };

  let optionScanTimer = null;
  const scheduleOptionScan = () => {
    clearTimeout(optionScanTimer);
    optionScanTimer = setTimeout(recordVisibleOptionSets, 60);
  };

  const optionObserver = new MutationObserver(scheduleOptionScan);
  optionObserver.observe(document.documentElement, {
    childList: true,
    subtree: true
  });
  document.addEventListener(
    "click",
    (event) => {
      rememberFieldContext(event.target);
      scheduleOptionScan();
    },
    true
  );
  document.addEventListener(
    "focusin",
    (event) => {
      rememberFieldContext(event.target);
      scheduleOptionScan();
    },
    true
  );

  const extractPage = (scenarioLabel) => {
    recordVisibleOptionSets();
    const nodes = Array.from(document.querySelectorAll(FIELD_SELECTOR));
    const deduplicated = nodes.filter((node, index) => nodes.indexOf(node) === index);
    const fields = deduplicated.map(describeField).filter((field) => field.visible);

    const openOptions = uniqueTexts(
      Array.from(document.querySelectorAll(OPTION_SELECTOR))
        .filter(isVisible)
        .map((node) => node.textContent),
      300
    );

    const pageMessages = uniqueTexts(
      Array.from(
        document.querySelectorAll(
          "[role='alert'], .ant-message-notice-content, .ant-notification-notice-message, .el-message, .toast, [class*='validation-summary']"
        )
      )
        .filter(isVisible)
        .map((node) => node.textContent),
      100
    );

    const actionButtons = uniqueTexts(
      Array.from(document.querySelectorAll("button, [role='button'], input[type='submit'], input[type='button']"))
        .filter(isVisible)
        .map((node) => node.textContent || node.getAttribute("aria-label") || node.getAttribute("title")),
      100
    );

    return {
      schemaVersion: 4,
      capturedAt: new Date().toISOString(),
      scenarioLabel: cleanText(scenarioLabel, 80),
      page: {
        path: location.pathname,
        protocol: location.protocol,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        scrollX: Math.round(window.scrollX),
        scrollY: Math.round(window.scrollY),
        formCount: document.forms.length,
        iframeCount: document.querySelectorAll("iframe").length
      },
      fields,
      openOptions,
      observedOptionSets: observedOptionSets.map((item) => ({ ...item, options: [...item.options] })),
      pageMessages,
      actionButtons,
      privacy: {
        fieldValuesCollected: false,
        cookiesCollected: false,
        localStorageCollected: false,
        networkTrafficCollected: false
      }
    };
  };

  const MASK_STYLE_ID = "product-form-capture-mask-style";
  const enableScreenshotMask = () => {
    if (document.getElementById(MASK_STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = MASK_STYLE_ID;
    style.textContent = `
      input:not([type='button']):not([type='submit']),
      textarea,
      [contenteditable='true'],
      [role='textbox'],
      [role='combobox'],
      .ant-select-selection-item,
      .el-select__selected-item,
      .el-input__inner,
      .ant-picker-input input {
        color: transparent !important;
        -webkit-text-fill-color: transparent !important;
        text-shadow: none !important;
        caret-color: transparent !important;
      }
    `;
    document.documentElement.appendChild(style);
  };

  const disableScreenshotMask = () => {
    document.getElementById(MASK_STYLE_ID)?.remove();
  };

  const waitFor = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

  const dispatchClick = (element) => {
    element.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
    element.click();
  };

  const setNativeTextValue = (element, value) => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (!setter) throw new Error("当前控件不支持标准文本回填。");
    element.focus();
    setter.call(element, value);
    element.dispatchEvent(
      new InputEvent("input", { bubbles: true, cancelable: false, inputType: "insertText", data: null })
    );
    element.dispatchEvent(new Event("change", { bubbles: true }));
    element.dispatchEvent(new FocusEvent("blur", { bubbles: true }));
  };

  const fieldElementByDescriptor = (descriptor) => {
    const fields = Array.from(document.querySelectorAll(FIELD_SELECTOR));
    return fields.find((element, index) => fieldIdentity(element, index) === descriptor.fieldKey) || null;
  };

  const visibleOptionNodes = () =>
    Array.from(document.querySelectorAll(OPTION_SELECTOR)).filter(isVisible);

  const fillCustomSelect = async (element, requestedText) => {
    if (!requestedText) throw new Error("请输入要选择的完整选项文字。");
    const component = element.closest(
      ".ivu-select, .ivu-cascader, .el-select, .el-cascader, .ant-select, .ant-cascader, [role='combobox']"
    );
    const trigger =
      component?.querySelector(
        ".ivu-select-selection, .ivu-cascader-rel, .el-input, .el-select__wrapper, .ant-select-selector, [role='combobox']"
      ) || component || element;
    dispatchClick(trigger);

    let options = [];
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await waitFor(120);
      options = visibleOptionNodes();
      if (options.length) break;
    }

    const exact = options.find((node) => cleanText(node.textContent, 300) === cleanText(requestedText, 300));
    if (!exact) {
      throw new Error(`下拉框已打开，但没有找到完全匹配的选项“${cleanText(requestedText, 80)}”。`);
    }
    dispatchClick(exact);
    return { method: "custom-select-click", matchedLabel: cleanText(exact.textContent, 160) };
  };

  const fillField = async (descriptor) => {
    const element = fieldElementByDescriptor(descriptor);
    if (!element) throw new Error("未找到目标字段。页面可能已刷新，请重新采集当前页。");
    if (element.matches(":disabled") || element.getAttribute("aria-disabled") === "true") {
      throw new Error("该字段当前为禁用状态，未执行回填。");
    }
    if (element.readOnly || element.getAttribute("aria-readonly") === "true") {
      throw new Error("该字段当前为只读状态，未执行回填。");
    }

    const inputType = (element.getAttribute("type") || element.getAttribute("role") || "").toLowerCase();
    if (["password", "file", "hidden", "submit", "button"].includes(inputType)) {
      throw new Error("该控件类型不允许通过回填测试操作。");
    }

    if (["radio", "checkbox", "switch"].includes(inputType) || element.matches("[role='radio'], [role='checkbox'], [role='switch']")) {
      dispatchClick(element);
      return { ok: true, method: "control-click", fieldKey: descriptor.fieldKey };
    }

    if (element instanceof HTMLSelectElement) {
      const requested = cleanText(descriptor.requestedValue, 300);
      const option = Array.from(element.options).find(
        (item) => cleanText(item.textContent, 300) === requested || cleanText(item.value, 300) === requested
      );
      if (!option) throw new Error(`没有找到选项“${requested}”。`);
      element.value = option.value;
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, method: "native-select", fieldKey: descriptor.fieldKey };
    }

    const customSelect = element.closest(
      ".ivu-select, .ivu-cascader, .el-select, .el-cascader, .ant-select, .ant-cascader"
    );
    if (customSelect || element.getAttribute("role") === "combobox") {
      const result = await fillCustomSelect(element, descriptor.requestedValue);
      return { ok: true, ...result, fieldKey: descriptor.fieldKey };
    }

    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      setNativeTextValue(element, String(descriptor.requestedValue || ""));
      return { ok: true, method: "text-events", fieldKey: descriptor.fieldKey };
    }

    throw new Error("该字段暂不支持回填测试。");
  };

  globalThis.__PRODUCT_FORM_CAPTURE_EXTRACT__ = extractPage;
  globalThis.__PRODUCT_FORM_CAPTURE_MASK__ = enableScreenshotMask;
  globalThis.__PRODUCT_FORM_CAPTURE_UNMASK__ = disableScreenshotMask;
  globalThis.__PRODUCT_FORM_CAPTURE_FILL__ = fillField;

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    try {
      if (message?.type === "EXTRACT_FORM_STATE") {
        sendResponse({ ok: true, capture: extractPage(message.scenarioLabel || "") });
        return;
      }
      if (message?.type === "MASK_FOR_SCREENSHOT") {
        enableScreenshotMask();
        sendResponse({ ok: true });
        return;
      }
      if (message?.type === "UNMASK_AFTER_SCREENSHOT") {
        disableScreenshotMask();
        sendResponse({ ok: true });
      }
    } catch (error) {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  });
})();
