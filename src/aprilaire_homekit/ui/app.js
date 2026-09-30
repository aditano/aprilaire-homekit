const title = document.querySelector("#title");
const lede = document.querySelector("#lede");
const banner = document.querySelector("#banner");
const finder = document.querySelector("#finder");
const dashboard = document.querySelector("#dashboard");
const scanCopy = document.querySelector("#scan-copy");
const bar = document.querySelector("#bar");
const devices = document.querySelector("#devices");
const empty = document.querySelector("#empty");
const scanButton = document.querySelector("#scan");
const manual = document.querySelector("#manual");
const pair = document.querySelector("#pair");
const temp = document.querySelector("#temp");
const mode = document.querySelector("#mode");
const model = document.querySelector("#model");
const thermoState = document.querySelector("#thermo-state");
const thermoDetail = document.querySelector("#thermo-detail");
const homeState = document.querySelector("#home-state");
const homeDetail = document.querySelector("#home-detail");
const toggleBridge = document.querySelector("#toggle-bridge");
const autostart = document.querySelector("#autostart");
const nameInput = document.querySelector("#accessory-name");
const logPath = document.querySelector("#log-path");

let deviceKey = "";
let scanRequested = false;
let busy = false;
let holdNotice = false;

async function api(path, body) {
  const options = { headers: { "X-Aprilaire-Home": "1" } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || "Something went wrong.");
  }
  return data;
}

function setBanner(text, hold = false) {
  holdNotice = hold;
  banner.hidden = !text;
  banner.textContent = text || "";
}

function render(status) {
  const chosen = Boolean(status.thermostat.host);
  finder.hidden = chosen;
  dashboard.hidden = !chosen;
  if (!chosen) {
    title.textContent = "Find your thermostat";
    lede.textContent = "This computer looks for an AprilAire thermostat on your Wi-Fi and adds it to the Apple Home app. Nothing is sent to a server.";
    renderScan(status);
    if (!status.scan.running && !scanRequested && status.scan.devices.length === 0 && !status.scan.finished) {
      scanRequested = true;
      api("/api/scan", {}).catch((error) => setBanner(error.message));
    }
  } else {
    title.textContent = status.accessory_name || "AprilAire";
    lede.textContent = "You can close this window. The connection stays on this computer.";
    renderDashboard(status);
  }
  const messages = [
    status.bridge.error,
    status.thermostat.last_error,
    status.scan.error,
    status.autostart.detail,
  ].filter(Boolean);
  if (!busy && !holdNotice) {
    setBanner(messages[0] || "");
  }
}

function renderScan(status) {
  const scan = status.scan;
  scanButton.disabled = scan.running;
  scanButton.textContent = scan.running ? "Scanning…" : "Scan again";
  if (scan.running) {
    const total = scan.total || 1;
    const pct = Math.min(100, Math.round((scan.scanned / total) * 100));
    bar.style.width = `${pct}%`;
    scanCopy.textContent = scan.total
      ? `Checking your network… ${scan.scanned} of ${scan.total}`
      : "Checking your network…";
  } else if (scan.finished) {
    bar.style.width = "100%";
    scanCopy.textContent = scan.devices.length
      ? `Found ${scan.devices.length}.`
      : "Scan finished.";
  } else {
    bar.style.width = "8%";
    scanCopy.textContent = "Looking for thermostats…";
  }

  const key = JSON.stringify(scan.devices);
  if (key !== deviceKey) {
    deviceKey = key;
    devices.replaceChildren();
    for (const device of scan.devices) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "device";
      const modelName = document.createElement("strong");
      modelName.textContent = device.model || "AprilAire thermostat";
      const meta = document.createElement("span");
      meta.textContent = `${device.host}:${device.port} · ${device.mac}`;
      const cta = document.createElement("em");
      cta.textContent = "Use this thermostat";
      button.append(modelName, meta, cta);
      button.addEventListener("click", () => choose(device));
      devices.append(button);
    }
  }

  empty.hidden = scan.running || !scan.finished || scan.devices.length > 0;
  empty.textContent = "No AprilAire thermostat answered. On the panel, hold Contractor Info for about 10 seconds and set Automation Enable to Automation System. Then scan again, or enter the IP address. The 8920W manual does not list that setting; if it is missing, local control is not available on that panel.";
}

function renderDashboard(status) {
  const thermo = status.thermostat;
  const home = status.homekit;
  model.textContent = thermo.model || "AprilAire thermostat";
  temp.textContent = thermo.current || "—";
  const bits = [thermo.mode, thermo.action].filter(Boolean);
  if (thermo.humidity != null && thermo.connected) {
    bits.push(`${thermo.humidity}% humidity`);
  }
  if (thermo.mode === "Auto" && thermo.heat && thermo.cool) {
    bits.push(`Heat ${thermo.heat} · Cool ${thermo.cool}`);
  }
  mode.textContent = bits.join(" · ") || (thermo.connected ? "Connected" : "Waiting for the thermostat");

  thermoState.textContent = thermo.connected ? "Connected" : "Not connected";
  thermoState.className = `pill ${thermo.connected ? "on" : "off"}`;
  thermoDetail.textContent = thermo.mac
    ? `${thermo.host} · ${thermo.mac}`
    : `${thermo.host}:${thermo.port}`;

  if (home.paired) {
    homeState.textContent = "Paired";
    homeState.className = "pill on";
    homeDetail.textContent = "The tile is in the Home app.";
  } else if (home.advertising) {
    homeState.textContent = "Ready to pair";
    homeState.className = "pill on";
    homeDetail.textContent = "Scan the code with the iPhone that uses this home.";
  } else {
    homeState.textContent = "Not advertising";
    homeState.className = "pill off";
    homeDetail.textContent = "Start the bridge to show the pairing code.";
  }

  toggleBridge.textContent = status.bridge.running ? "Stop bridge" : "Start bridge";
  if (document.activeElement !== autostart) {
    autostart.checked = Boolean(status.autostart.enabled);
  }
  if (document.activeElement !== nameInput) {
    nameInput.value = status.accessory_name || "";
  }
  document.querySelector("#unit-f").setAttribute("aria-pressed", thermo.display_unit === "F" ? "true" : "false");
  document.querySelector("#unit-c").setAttribute("aria-pressed", thermo.display_unit === "C" ? "true" : "false");
  logPath.textContent = `Log: ${status.log_path}`;
  renderPair(home);
}

function renderPair(home) {
  if (home.paired) {
    pair.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = "Added to Apple Home";
    const copy = document.createElement("p");
    copy.textContent = "The thermostat should show up as a tile. Changing the temperature in Home sends it straight to the panel over your Wi-Fi.";
    pair.append(heading, copy);
    return;
  }
  if (!home.advertising || !home.pin) {
    pair.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = "Pairing code";
    const copy = document.createElement("p");
    copy.textContent = "The code appears here when the bridge is running.";
    pair.append(heading, copy);
    return;
  }
  const signature = `${home.pin}|${home.uri}`;
  if (pair.dataset.signature === signature) {
    return;
  }
  pair.dataset.signature = signature;
  pair.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = "Add to Apple Home";
  const steps = document.createElement("ol");
  ["Open the Home app on your iPhone.", "Tap +, then Add Accessory.", "Scan this code. If the camera will not scan it, tap More options and enter the code."].forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    steps.append(item);
  });
  const qr = document.createElement("div");
  qr.id = "qr";
  if (home.qr_svg && home.qr_svg.includes("<svg") && !home.qr_svg.toLowerCase().includes("<script")) {
    qr.innerHTML = home.qr_svg;
  }
  const pin = document.createElement("p");
  pin.className = "pin";
  pin.textContent = home.pin;
  pair.append(heading, steps, qr, pin);
}

async function choose(device) {
  busy = true;
  setBanner("Connecting…");
  try {
    await api("/api/select", { host: device.host, port: device.port });
    setBanner("");
  } catch (error) {
    setBanner(error.message, true);
  } finally {
    busy = false;
  }
}

scanButton.addEventListener("click", () => {
  scanRequested = true;
  setBanner("");
  api("/api/scan", {}).catch((error) => setBanner(error.message, true));
});

manual.addEventListener("submit", async (event) => {
  event.preventDefault();
  const host = document.querySelector("#manual-host").value.trim();
  const port = Number(document.querySelector("#manual-port").value || "8000");
  busy = true;
  setBanner("Checking that address…");
  try {
    const result = await api("/api/probe", { host, port });
    await api("/api/select", { host: result.device.host, port: result.device.port });
    setBanner("");
  } catch (error) {
    setBanner(error.message, true);
  } finally {
    busy = false;
  }
});

toggleBridge.addEventListener("click", async () => {
  const stop = toggleBridge.textContent.startsWith("Stop");
  try {
    await api(stop ? "/api/stop" : "/api/start", {});
    setBanner("");
  } catch (error) {
    setBanner(error.message, true);
  }
});

autostart.addEventListener("change", async () => {
  try {
    await api("/api/autostart", { enabled: autostart.checked });
    setBanner("");
  } catch (error) {
    setBanner(error.message, true);
  }
});

nameInput.addEventListener("change", async () => {
  try {
    await api("/api/name", { name: nameInput.value });
    setBanner("");
  } catch (error) {
    setBanner(error.message, true);
  }
});

document.querySelector("#unit-f").addEventListener("click", () => api("/api/display-unit", { unit: "F" }).catch((error) => setBanner(error.message, true)));
document.querySelector("#unit-c").addEventListener("click", () => api("/api/display-unit", { unit: "C" }).catch((error) => setBanner(error.message, true)));

document.querySelector("#forget").addEventListener("click", async () => {
  try {
    await api("/api/forget", {});
    deviceKey = "";
    scanRequested = false;
    pair.dataset.signature = "";
  } catch (error) {
    setBanner(error.message, true);
  }
});

document.querySelector("#reset").addEventListener("click", async () => {
  const sure = window.confirm("Remove the thermostat from the Home app first. Resetting here makes a new pairing, and the old tile will stop working.");
  if (!sure) {
    return;
  }
  try {
    await api("/api/reset-pairing", {});
    pair.dataset.signature = "";
  } catch (error) {
    setBanner(error.message, true);
  }
});

document.querySelector("#quit").addEventListener("click", async () => {
  try {
    await api("/api/quit", {});
    setBanner("AprilAire Home has quit. You can close this window. If login start is on, it comes back the next time you log in.", true);
  } catch (error) {
    setBanner(error.message, true);
  }
});

async function refresh() {
  try {
    render(await api("/api/status"));
  } catch (error) {
    if (!busy) {
      setBanner("Can’t reach AprilAire Home on this computer. If you just quit it, you can close this window.");
    }
  }
}

refresh();
setInterval(refresh, 1500);
