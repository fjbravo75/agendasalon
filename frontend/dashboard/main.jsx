import { createRoot } from "react-dom/client";

import SuperadminDashboard from "./SuperadminDashboard.jsx";
import "./dashboard.css";

const mountNode = document.getElementById("superadmin-dashboard-root");
const configNode = document.getElementById("superadmin-dashboard-config");

if (mountNode && configNode) {
  const config = JSON.parse(configNode.textContent);
  createRoot(mountNode).render(<SuperadminDashboard config={config} />);
}
