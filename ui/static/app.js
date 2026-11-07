function apiKey() {
  return sessionStorage.getItem("apiKey") || "";
}

document.getElementById("key-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sessionStorage.setItem("apiKey", document.getElementById("api-key").value.trim());
  document.getElementById("api-key").value = "";
});

async function api(path, options = {}) {
  const headers = Object.assign(
    { "content-type": "application/json" },
    options.headers || {},
  );
  const key = apiKey();
  if (key) headers.authorization = "Bearer " + key;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = body && body.error ? body.error.message : response.statusText;
    throw new Error(message);
  }
  return body;
}
