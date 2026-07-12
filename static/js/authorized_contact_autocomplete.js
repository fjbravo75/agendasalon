(() => {
  const forms = document.querySelectorAll("[data-authorized-person-form]");

  const formatPhone = (value) => {
    const digits = String(value || "").replace(/\D/g, "");
    const localDigits = digits.length === 11 && digits.startsWith("34") ? digits.slice(2) : digits;
    return localDigits.length === 9
      ? `${localDigits.slice(0, 3)} ${localDigits.slice(3, 6)} ${localDigits.slice(6)}`
      : value || "";
  };

  const statusLabel = (client) => {
    if (!client.has_phone) return "Sin teléfono propio";
    if (client.online_status === "active") return "Cuenta online activa";
    if (client.online_status === "inactive") return "Cuenta online pausada";
    return "Sin cuenta online";
  };

  forms.forEach((form) => {
    const searchUrl = form.dataset.clientSearchUrl;
    const searchInput = form.querySelector("[data-client-search-input]");
    const selectedId = form.querySelector("[data-client-search-id]");
    const results = form.querySelector("[data-client-search-results]");
    const selection = form.querySelector("[data-client-selection]");
    const selectedName = form.querySelector("[data-selected-name]");
    const selectedPhone = form.querySelector("[data-selected-phone]");
    const selectedStatus = form.querySelector("[data-selected-account-status]");
    const onlineToggle = form.querySelector("[data-online-toggle]");
    const onlineHelp = form.querySelector("[data-online-help]");
    const typeInputs = [...form.querySelectorAll('[name="contact_type"]')];
    const sources = [...form.querySelectorAll("[data-contact-source]")];
    let matches = [];
    let activeIndex = -1;
    let requestController = null;
    let debounceTimer = null;

    if (!searchUrl || !searchInput || !selectedId || !results) return;

    const closeResults = () => {
      results.hidden = true;
      results.replaceChildren();
      matches = [];
      activeIndex = -1;
      searchInput.setAttribute("aria-expanded", "false");
      searchInput.removeAttribute("aria-activedescendant");
    };

    const clearSelection = () => {
      selectedId.value = "";
      if (selection) selection.dataset.onlineActive = "false";
      selection?.classList.add("is-empty");
      if (selectedName) selectedName.textContent = "Ninguna todavía";
      if (selectedPhone) selectedPhone.value = "";
      if (selectedStatus) selectedStatus.textContent = "Elige un resultado para continuar";
      if (onlineToggle) {
        onlineToggle.checked = false;
        onlineToggle.disabled = true;
      }
      if (onlineHelp) {
        onlineHelp.textContent = "Disponible cuando la persona tenga una cuenta online activa.";
      }
    };

    const selectClient = (client) => {
      selectedId.value = String(client.id);
      searchInput.value = client.name;
      selection?.classList.remove("is-empty");
      if (selectedName) selectedName.textContent = client.name;
      if (selectedPhone) selectedPhone.value = formatPhone(client.phone) || "Sin teléfono propio";
      if (selectedStatus) selectedStatus.textContent = statusLabel(client);
      const canBookOnline = client.has_phone && client.online_status === "active";
      if (selection) selection.dataset.onlineActive = canBookOnline ? "true" : "false";
      if (onlineToggle) {
        onlineToggle.disabled = !canBookOnline;
        if (!canBookOnline) onlineToggle.checked = false;
      }
      if (onlineHelp) {
        onlineHelp.textContent = canBookOnline
          ? "Podrá elegir esta ficha desde su cuenta."
          : "Disponible cuando la persona tenga una cuenta online activa.";
      }
      closeResults();
    };

    const setActiveResult = (index) => {
      const buttons = [...results.querySelectorAll("button")];
      if (!buttons.length) return;
      activeIndex = (index + buttons.length) % buttons.length;
      buttons.forEach((button, buttonIndex) => {
        button.classList.toggle("is-active", buttonIndex === activeIndex);
      });
      const activeButton = buttons[activeIndex];
      searchInput.setAttribute("aria-activedescendant", activeButton.id);
      activeButton.scrollIntoView({ block: "nearest" });
    };

    const renderResults = (clients) => {
      results.replaceChildren();
      matches = clients;
      activeIndex = -1;
      if (!clients.length) {
        const empty = document.createElement("p");
        empty.className = "authorized-client-results__empty";
        empty.textContent = "No hay clientes que coincidan.";
        results.append(empty);
      } else {
        clients.forEach((client, index) => {
          const button = document.createElement("button");
          button.type = "button";
          button.id = `authorized-client-option-${client.id}`;
          button.setAttribute("role", "option");
          button.dataset.resultIndex = String(index);

          const identity = document.createElement("span");
          const name = document.createElement("strong");
          name.textContent = client.name;
          const phone = document.createElement("small");
          phone.textContent = formatPhone(client.phone) || "Sin teléfono propio";
          identity.append(name, phone);

          const account = document.createElement("small");
          account.className = `authorized-client-results__status is-${client.online_status}`;
          account.textContent = statusLabel(client);
          button.append(identity, account);
          button.addEventListener("click", () => selectClient(client));
          results.append(button);
        });
      }
      results.hidden = false;
      searchInput.setAttribute("aria-expanded", "true");
    };

    const searchClients = async () => {
      const query = searchInput.value.trim();
      if (query.length < 2) {
        closeResults();
        return;
      }
      requestController?.abort();
      requestController = new AbortController();
      try {
        const response = await fetch(`${searchUrl}?q=${encodeURIComponent(query)}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        });
        if (!response.ok) throw new Error("client-search-failed");
        const payload = await response.json();
        renderResults(payload.results || []);
      } catch (error) {
        if (error.name !== "AbortError") {
          renderResults([]);
        }
      }
    };

    searchInput.addEventListener("input", () => {
      clearSelection();
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(searchClients, 180);
    });

    searchInput.addEventListener("keydown", (event) => {
      if (results.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveResult(activeIndex + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveResult(activeIndex - 1);
      } else if (event.key === "Enter" && activeIndex >= 0) {
        event.preventDefault();
        selectClient(matches[activeIndex]);
      } else if (event.key === "Escape") {
        closeResults();
      }
    });

    document.addEventListener("click", (event) => {
      if (!form.contains(event.target)) closeResults();
    });

    const applyContactType = () => {
      const selectedType = typeInputs.find((input) => input.checked)?.value || "registered";
      sources.forEach((source) => {
        const isActive = source.dataset.contactSource === selectedType;
        source.hidden = !isActive;
        source.querySelectorAll("input, select, textarea").forEach((control) => {
          control.disabled = !isActive;
        });
      });
      if (selectedType === "registered" && onlineToggle) {
        onlineToggle.disabled = selection?.dataset.onlineActive !== "true";
      }
      if (selectedType === "external") {
        closeResults();
      }
    };

    typeInputs.forEach((input) => input.addEventListener("change", applyContactType));
    applyContactType();

    if (!selectedId.value) clearSelection();
  });
})();
