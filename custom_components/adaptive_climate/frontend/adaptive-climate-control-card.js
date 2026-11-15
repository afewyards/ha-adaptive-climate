if (!customElements.get("adaptive-climate-control-card")) {
  customElements.define(
    "adaptive-climate-control-card",
    class extends HTMLElement {
      setConfig() {}
      set hass(_) {
        this.textContent = "Adaptive Climate control card: not built yet";
      }
    }
  );
}
