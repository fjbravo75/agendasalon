(() => {
  const form = document.querySelector("[data-booking-search]");
  if (!form) {
    return;
  }

  const services = [...form.querySelectorAll("[data-booking-service]")];
  const countNode = form.querySelector("[data-booking-count]");
  const totalNode = form.querySelector("[data-booking-total]");

  const updateSummary = () => {
    const selected = services.filter((service) => service.checked);
    const duration = selected.reduce(
      (total, service) => total + Number(service.dataset.duration || 0),
      0,
    );
    const priced = selected.filter((service) => service.dataset.price !== "");
    const price = priced.reduce(
      (total, service) => total + Number(service.dataset.price || 0),
      0,
    );
    const hasUnpriced = priced.length !== selected.length;

    if (!selected.length) {
      countNode.textContent = "Ningún servicio seleccionado";
      totalNode.textContent = "Selecciona al menos uno";
      return;
    }

    countNode.textContent = `${selected.length} ${selected.length === 1 ? "servicio" : "servicios"} · ${duration} min`;
    if (!priced.length) {
      totalNode.textContent = "Precio a consultar";
      return;
    }
    totalNode.textContent = `${hasUnpriced ? "Desde " : ""}${price.toLocaleString("es-ES", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} €`;
  };

  services.forEach((service) => service.addEventListener("change", updateSummary));
  updateSummary();
})();
