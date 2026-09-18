const $ = (id) => document.getElementById(id);
let me = null;
let view = "home";
let customers = [];
let activeThread = null;

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.style.display = "block";
  setTimeout(() => (t.style.display = "none"), 2800);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function copy(text) {
  navigator.clipboard.writeText(text).then(
    () => toast("Link copied — send it to them"),
    () => toast(text)
  );
}

function stars(n) {
  return `<span class="stars">${"★".repeat(n)}${"☆".repeat(5 - n)}</span>`;
}

async function boot() {
  try {
    me = await api("/api/me");
    $("login").classList.add("hidden");
    $("shell").classList.remove("hidden");
    $("who").textContent = me.user.name;
    $("loc-name").textContent = (me.locations[0] || {}).name || "Starling";
    $("plan-badge").textContent = ((me.locations[0] || {}).plan || "starter").toUpperCase();
    render();
  } catch {
    $("login").classList.remove("hidden");
    $("shell").classList.add("hidden");
  }
}

let authMode = "signup";
$("login-btn").onclick = async () => {
  if (authMode === "signup") {
    authMode = "login";
    $("auth-title").textContent = "Sign in";
    $("auth-lead").textContent = "Welcome back.";
    $("signup-fields").classList.add("hidden");
    $("signup-btn").classList.add("hidden");
    $("login-btn").textContent = "Create an account instead";
    $("login-btn").classList.remove("ghost");
    $("login-btn").classList.add("primary");
    return;
  }
  try {
    await api("/api/auth/login", { method: "POST", body: { email: $("email").value, password: $("password").value } });
    await boot();
  } catch (e) {
    $("login-err").textContent = e.message;
  }
};
$("signup-btn").onclick = async () => {
  try {
    await api("/api/auth/signup", {
      method: "POST",
      body: {
        name: $("sname").value,
        email: $("email").value,
        password: $("password").value,
        business: $("sbusiness").value,
      },
    });
    await boot();
  } catch (e) {
    $("login-err").textContent = e.message;
  }
};
$("logout").onclick = async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.reload();
};

$("more-toggle").onclick = () => $("more-nav").classList.toggle("hidden");

document.querySelectorAll(".navb[data-view]").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".navb").forEach((x) => x.classList.remove("on"));
    b.classList.add("on");
    view = b.dataset.view;
    render();
  };
});

async function render() {
  const main = $("main");
  main.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    if (view === "home" || view === "reviews" || view === "automations") return home();
    if (view === "inbox") return inbox();
    if (view === "listings") return listings();
    if (view === "social") return social();
    if (view === "pages") return pages();
    if (view === "settings") return settings();
    if (view === "billing") return billing();
    if (view === "agency") return agency();
  } catch (e) {
    main.innerHTML = `<div class="card"><p>${e.message}</p></div>`;
  }
}

async function home() {
  const d = await api("/api/dashboard");
  $("loc-name").textContent = d.location.name;
  $("plan-badge").textContent = d.location.plan.toUpperCase();
  const asked = d.stats.requests || 0;
  const unreplied = (d.recent || []).filter((r) => !r.reply);
  const pending = d.pending || [];
  const priv = d.private || [];
  const emailed = d.delivery && d.delivery.email;
  const sms = d.delivery && d.delivery.sms;

  $("main").innerHTML = `
    <div class="how">
      <div><b>1. Ask</b><span>Send a link after they visit.</span></div>
      <div><b>2. They rate</b><span>4–5★ go public. 1–3★ stay private.</span></div>
      <div><b>3. You tap send</b><span>Starling writes the reply.</span></div>
    </div>

    <div class="card ask">
      <h2>Ask for a review</h2>
      <p class="muted">Name + how to reach them. That’s it. ${
        emailed || sms
          ? "We’ll send the link for you."
          : "You’ll get a link to copy — we don’t have email/SMS connected yet."
      }</p>
      <div class="row">
        <input id="ask-name" placeholder="Customer name" autocomplete="name"/>
        <input id="ask-reach" placeholder="Email or phone"/>
        <button class="btn primary" id="ask-go">Ask for a review</button>
      </div>
      <p class="muted" id="ask-out"></p>
    </div>

    ${
      pending.length
        ? `<div class="card">
            <h3>Waiting on them</h3>
            ${pending
              .map(
                (p) => `<div class="wait">
                  <div><strong>${p.customer_name}</strong> <span class="muted">${p.status === "sent" ? "sent" : "link ready"}</span></div>
                  <button class="btn ghost" data-copy="${p.link}">Copy link</button>
                  <a class="btn ghost" href="${p.link}" target="_blank">Open</a>
                </div>`
              )
              .join("")}
          </div>`
        : asked
          ? ""
          : `<p class="muted">No asks yet. Start with someone who was just in.</p>`
    }

    ${
      unreplied.length
        ? `<div class="card">
            <h3>Needs a reply</h3>
            ${unreplied
              .map(
                (r) => `<div class="todo">
                  <div>${stars(r.rating)} <strong>${r.customer_name || "Guest"}</strong>
                    ${r.public ? "" : "<span class='badge warn'>private</span>"}</div>
                  <p>${r.text || "<span class='muted'>No comment</span>"}</p>
                  ${r.public
                    ? `<button class="btn primary" data-sendai="${r.id}">Send AI reply</button>`
                    : `<button class="btn ghost" data-inbox="1">Open inbox</button>`}
                </div>`
              )
              .join("")}
          </div>`
        : ""
    }

    ${
      priv.length
        ? `<div class="card">
            <h3>Private — fix these first</h3>
            <p class="muted">Low ratings never went public. Answer them in Inbox.</p>
            <button class="btn primary" data-inbox="1">Open inbox</button>
          </div>`
        : ""
    }

    <div class="stats">
      <div class="stat"><small>Public rating</small><b>${d.stats.avg || 0}★</b></div>
      <div class="stat"><small>Public reviews</small><b>${d.stats.reviews}</b></div>
      <div class="stat"><small>Asks sent</small><b>${asked}</b></div>
    </div>
  `;

  $("ask-go").onclick = sendAsk;
  $("ask-name").onkeydown = (e) => {
    if (e.key === "Enter") sendAsk();
  };
  $("ask-reach").onkeydown = (e) => {
    if (e.key === "Enter") sendAsk();
  };
  document.querySelectorAll("[data-copy]").forEach((b) => {
    b.onclick = () => copy(b.dataset.copy);
  });
  document.querySelectorAll("[data-sendai]").forEach((b) => {
    b.onclick = async () => {
      b.disabled = true;
      b.textContent = "Writing…";
      try {
        await api(`/api/reviews/${b.dataset.sendai}/send-ai`, { method: "POST" });
        toast("Reply saved");
        home();
      } catch (e) {
        b.disabled = false;
        b.textContent = "Send AI reply";
        toast(e.message);
      }
    };
  });
  document.querySelectorAll("[data-inbox]").forEach((b) => {
    b.onclick = () => {
      view = "inbox";
      document.querySelectorAll(".navb").forEach((x) => x.classList.remove("on"));
      document.querySelector('.navb[data-view="inbox"]').classList.add("on");
      render();
    };
  });
}

async function sendAsk() {
  const name = $("ask-name").value.trim();
  const reach = $("ask-reach").value.trim();
  if (!name) return toast("Add their name");
  const email = reach.includes("@") ? reach : "";
  const phone = email ? "" : reach;
  $("ask-go").disabled = true;
  try {
    const d = await api("/api/ask-review", { method: "POST", body: { name, email, phone } });
    const how = d.sent ? "Sent." : "Link ready — copy and send it.";
    toast(how);
    await home();
    const out = $("ask-out");
    if (out) {
      out.innerHTML = `${how} <button class="btn ghost" type="button" id="copy-now">Copy link</button>
        <a href="${d.link}" target="_blank">${d.link}</a>`;
      $("copy-now").onclick = () => copy(d.link);
    }
  } catch (e) {
    $("ask-out").textContent = e.message;
    $("ask-go").disabled = false;
  }
}

async function inbox() {
  const d = await api("/api/inbox");
  $("main").innerHTML = `
    <h2>Inbox</h2>
    <p class="muted">Private low-star reviews, website chat, and forms land here.</p>
    <div class="split">
      <div class="list" id="threads">
        ${
          d.threads.length
            ? d.threads
                .map(
                  (t) => `<button data-id="${t.id}"><strong>${t.name}</strong><br><span class="muted">${t.channel} · ${t.preview || ""}</span></button>`
                )
                .join("")
            : "<p class='muted' style='padding:12px'>Nothing yet. Private reviews and chat will show up here.</p>"
        }
      </div>
      <div class="thread" id="pane"><p class="muted">Pick a conversation</p></div>
    </div>`;
  document.querySelectorAll("#threads button").forEach((b) => {
    b.onclick = () => openThread(Number(b.dataset.id));
  });
  if (d.threads[0]) openThread(d.threads[0].id);
}

async function openThread(id) {
  activeThread = id;
  const d = await api(`/api/inbox/${id}`);
  $("pane").innerHTML = `
    <strong>${d.thread.name}</strong> <span class="muted">${d.thread.channel}</span>
    <div id="msgs">${d.messages.map((m) => `<div class="bubble ${m.direction}">${m.body}</div>`).join("")}</div>
    <textarea id="reply" placeholder="Write a reply"></textarea>
    <div class="row">
      <button class="btn ghost" id="ai">Write with AI</button>
      <button class="btn primary" id="send">Send</button>
    </div>`;
  $("ai").onclick = async () => {
    try {
      const r = await api(`/api/inbox/${id}/ai-reply`, { method: "POST" });
      $("reply").value = r.text;
    } catch (e) {
      toast(e.message);
    }
  };
  $("send").onclick = async () => {
    await api(`/api/inbox/${id}/reply`, { method: "POST", body: { body: $("reply").value } });
    toast("Sent");
    openThread(id);
  };
}

async function listings() {
  const d = await api("/api/listings");
  $("main").innerHTML = `
    <h2>Listings</h2>
    <p class="muted">Your name, address, and phone should match everywhere people search.</p>
    <div class="card">
      <p>${d.nap.name}<br>${d.nap.address || "Add your address in Settings"}, ${d.nap.city || ""} ${d.nap.state || ""} ${d.nap.zip || ""}<br>${d.nap.phone || ""}</p>
      <div class="row">
        <button class="btn primary" id="sync">Scan listings</button>
      </div>
    </div>
    <table class="table">
      <tr><th>Directory</th><th>Status</th></tr>
      ${d.listings
        .map(
          (l) => `<tr><td>${l.directory}</td><td><span class="badge ${l.status === "synced" ? "ok" : l.status === "missing" ? "bad" : "warn"}">${l.status}</span></td></tr>`
        )
        .join("")}
    </table>`;
  $("sync").onclick = async () => {
    await api("/api/listings/sync", { method: "POST" });
    toast("Scan finished");
    listings();
  };
}

async function social() {
  try {
    const d = await api("/api/social");
    $("main").innerHTML = `
      <h2>Social</h2>
      <div class="card">
        <textarea id="post" placeholder="Write a post"></textarea>
        <div class="row">
          <select id="plat"><option>instagram</option><option>facebook</option><option>google</option></select>
          <button class="btn ghost" id="ai">Generate</button>
          <button class="btn primary" id="save">Save</button>
        </div>
      </div>
      <table class="table">
        ${d.posts.map((p) => `<tr><td>${p.platform}</td><td>${p.body}</td><td>${p.status}</td></tr>`).join("")}
      </table>`;
    $("ai").onclick = async () => {
      const r = await api("/api/social/ai", { method: "POST" });
      $("post").value = r.text;
    };
    $("save").onclick = async () => {
      await api("/api/social", { method: "POST", body: { body: $("post").value, platform: $("plat").value, status: "scheduled" } });
      toast("Saved");
      social();
    };
  } catch (e) {
    $("main").innerHTML = `<div class="card"><h3>Social is on Growth</h3><p>${e.message}</p></div>`;
  }
}

async function pages() {
  const slug = (me.locations[0] || {}).slug;
  const forms = await api("/api/forms");
  $("main").innerHTML = `
    <h2>Your public page</h2>
    <div class="card">
      <p>${slug ? `<a href="/p/${slug}" target="_blank">Open your page</a>` : "Save your shop name in Settings first."}</p>
    </div>
    <div class="card">
      <h3>A form</h3>
      <div class="row">
        <input id="fname" placeholder="Contact form"/>
        <button class="btn primary" id="mkform">Create</button>
      </div>
      ${(forms.forms || []).map((f) => `<p><a href="/f/${f.slug}" target="_blank">${f.name}</a></p>`).join("") || ""}
    </div>`;
  $("mkform").onclick = async () => {
    const d = await api("/api/forms", { method: "POST", body: { name: $("fname").value || "Contact", fields: "name,email,phone,message" } });
    window.open(d.url, "_blank");
    pages();
  };
}

async function settings() {
  const d = await api("/api/dashboard");
  const loc = d.location;
  const slug = loc.slug;
  $("main").innerHTML = `
    <h2>Your shop</h2>
    <div class="card">
      <label>Business name</label><input id="lname" value="${loc.name || ""}"/>
      <label>Phone</label><input id="lphone" value="${loc.phone || ""}"/>
      <label>Address</label><input id="laddr" value="${loc.address || ""}"/>
      <label>City</label><input id="lcity" value="${loc.city || ""}"/>
      <label>State</label><input id="lst" value="${loc.state || ""}"/>
      <label>ZIP</label><input id="lzip" value="${loc.zip || ""}"/>
      <label>Google Place ID <span class="muted">(so 5★ reviews can open Google)</span></label>
      <input id="lplace" value="${loc.place_id || ""}" placeholder="ChIJ... from Google Maps"/>
      <button class="btn primary" id="lsave" style="margin-top:10px">Save</button>
    </div>
    <div class="card">
      <h3>Your public page</h3>
      <p>${slug ? `<a href="/p/${slug}" target="_blank">/p/${slug}</a>` : ""}</p>
      <p class="muted">Email auto-send: ${d.delivery.email ? "on" : "off"}. SMS auto-send: ${d.delivery.sms ? "on" : "off"}.</p>
    </div>`;
  $("lsave").onclick = async () => {
    await api("/api/location", {
      method: "PATCH",
      body: {
        name: $("lname").value,
        phone: $("lphone").value,
        address: $("laddr").value,
        city: $("lcity").value,
        state: $("lst").value,
        zip: $("lzip").value,
        place_id: $("lplace").value,
      },
    });
    toast("Saved");
    me = await api("/api/me");
    settings();
  };
}

async function billing() {
  const d = await api("/api/usage");
  $("main").innerHTML = `
    <h2>Usage</h2>
    <div class="stats">
      <div class="stat"><small>SMS cost</small><b>$${d.sms_cost}</b></div>
      <div class="stat"><small>Units</small><b>${d.totals.units}</b></div>
    </div>`;
}

async function agency() {
  if (me.user.role !== "agency") {
    $("main").innerHTML = `<div class="card"><p>Agency tools are for the owner account.</p></div>`;
    return;
  }
  const d = await api("/api/agency/revenue");
  $("main").innerHTML = `
    <h2>Agency</h2>
    <div class="stats">
      <div class="stat"><small>Software MRR</small><b>$${d.mrr}</b></div>
    </div>`;
}

boot();
