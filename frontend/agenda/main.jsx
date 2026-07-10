import { createRoot } from "react-dom/client";

import ProfessionalAgenda from "./ProfessionalAgenda.jsx";
import "./agenda.css";

const mountNode = document.getElementById("professional-agenda-root");
const configNode = document.getElementById("professional-agenda-config");

if (mountNode && configNode) {
  const config = JSON.parse(configNode.textContent);
  createRoot(mountNode).render(<ProfessionalAgenda config={config} />);
}
