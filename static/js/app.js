document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-confirm-message]");
  if (!form) {
    return;
  }

  const message = form.dataset.confirmMessage;
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

const appointmentClient = document.querySelector('[name="business_client"]');
const appointmentRequester = document.querySelector('[name="requested_by_contact"]');
if (appointmentClient && appointmentRequester) {
  appointmentClient.addEventListener("change", () => {
    appointmentRequester.value = "self";
  });
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) {
    return;
  }

  const input = document.getElementById(button.dataset.copyTarget);
  const status = button.parentElement.querySelector("[data-copy-status]");
  if (!input) {
    return;
  }

  try {
    await navigator.clipboard.writeText(input.value);
    if (status) {
      status.textContent = button.dataset.copySuccess || "Copiado";
    }
  } catch (_error) {
    input.focus();
    input.select();
    if (status) {
      status.textContent = "Selecciona y copia el enlace";
    }
  }
});

const appointmentSearchForm = document.querySelector("[data-appointment-search]");

if (appointmentSearchForm) {
  const serviceInputs = [
    ...appointmentSearchForm.querySelectorAll("[data-appointment-service]"),
  ];
  const totalNode = appointmentSearchForm.querySelector(
    "[data-appointment-duration-total]",
  );
  const detailNode = appointmentSearchForm.querySelector(
    "[data-appointment-duration-detail]",
  );
  const adjustmentHelpNode = appointmentSearchForm.querySelector(
    "[data-duration-adjust-help]",
  );

  const updateAppointmentDuration = () => {
    const selectedServices = serviceInputs.filter((input) => input.checked);
    const totalMinutes = selectedServices.reduce(
      (total, input) => total + Number(input.dataset.duration || 0),
      0,
    );

    if (!selectedServices.length) {
      totalNode.textContent = "0 min";
      detailNode.textContent = "Selecciona servicios para calcular el tiempo total.";
      adjustmentHelpNode.textContent =
        "Úsalo solo si la cita necesita más o menos tiempo que la suma de los servicios.";
      return;
    }

    totalNode.textContent = `${totalMinutes} min`;
    detailNode.textContent = `${selectedServices.length} ${
      selectedServices.length === 1 ? "servicio seleccionado" : "servicios seleccionados"
    }.`;
    adjustmentHelpNode.textContent =
      `La duración calculada es de ${totalMinutes} min. Ajústala solo si esta cita necesita un tiempo diferente.`;
  };

  serviceInputs.forEach((input) =>
    input.addEventListener("change", updateAppointmentDuration),
  );
  updateAppointmentDuration();
}

const serviceColorPicker = document.querySelector("[data-service-color-picker]");

if (serviceColorPicker) {
  const colorInput = serviceColorPicker.parentElement.querySelector(
    'input[name="color_hex"]',
  );
  const previewNode = serviceColorPicker.querySelector("[data-service-color-preview]");
  const nameNode = serviceColorPicker.querySelector("[data-service-color-name]");
  const colorOptions = [
    ...serviceColorPicker.querySelectorAll("[data-service-color-option]"),
  ];

  const selectServiceColor = (color, colorName) => {
    colorInput.value = color;
    serviceColorPicker.style.setProperty("--selected-service-color", color);
    previewNode.style.setProperty("--selected-service-color", color);
    nameNode.textContent = colorName;
    colorOptions.forEach((option) =>
      option.setAttribute("aria-pressed", String(option.dataset.color === color)),
    );
  };

  const initialOption =
    colorOptions.find((option) => option.dataset.color === colorInput.value.toUpperCase()) ||
    colorOptions[0];
  selectServiceColor(initialOption.dataset.color, initialOption.dataset.colorName);

  colorOptions.forEach((option) => {
    option.addEventListener("click", () => {
      selectServiceColor(option.dataset.color, option.dataset.colorName);
      serviceColorPicker.open = false;
    });
  });
}

const serviceScrollLists = [
  ...document.querySelectorAll("[data-service-scroll-list]"),
];

serviceScrollLists.forEach((serviceList) => {
  const visibleRows = [...serviceList.children]
    .filter((child) => child.classList.contains("service-row"))
    .slice(0, 5);

  const fitFiveServiceRows = () => {
    if (visibleRows.length < 5) {
      return;
    }

    const listStyles = window.getComputedStyle(serviceList);
    const rowGap = Number.parseFloat(listStyles.rowGap || listStyles.gap) || 0;
    const visibleHeight = visibleRows.reduce(
      (height, row) => height + row.getBoundingClientRect().height,
      rowGap * (visibleRows.length - 1),
    );

    serviceList.style.setProperty(
      "--service-catalog-visible-height",
      `${Math.ceil(visibleHeight)}px`,
    );
  };

  fitFiveServiceRows();
  document.fonts?.ready.then(fitFiveServiceRows);

  if ("ResizeObserver" in window) {
    const serviceRowsObserver = new ResizeObserver(fitFiveServiceRows);
    visibleRows.forEach((row) => serviceRowsObserver.observe(row));
  } else {
    window.addEventListener("resize", fitFiveServiceRows);
  }
});

const publicImagePreview = document.querySelector("[data-public-image-preview]");

if (publicImagePreview) {
  const imageChoices = [...document.querySelectorAll("[data-public-image-choice]")];
  const imageUpload = document.querySelector("[data-public-image-upload]");
  const imageFilename = document.querySelector("[data-public-image-filename]");
  let localPreviewUrl = "";

  const showPublicImage = (url) => {
    publicImagePreview.style.setProperty("--settings-public-bg", `url("${url}")`);
  };

  imageChoices.forEach((choice) => {
    choice.addEventListener("change", () => {
      if (!choice.checked) {
        return;
      }
      showPublicImage(choice.dataset.imageUrl);
    });
  });

  imageUpload?.addEventListener("change", () => {
    const [file] = imageUpload.files;
    if (!file) {
      if (imageFilename) {
        imageFilename.textContent = "Ningún archivo seleccionado";
      }
      return;
    }
    if (imageFilename) {
      imageFilename.textContent = file.name;
    }
    if (localPreviewUrl) {
      URL.revokeObjectURL(localPreviewUrl);
    }
    localPreviewUrl = URL.createObjectURL(file);
    showPublicImage(localPreviewUrl);
  });
}
