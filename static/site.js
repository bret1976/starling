const burger = document.getElementById("burger");
if (burger) burger.onclick = () => document.body.classList.toggle("nav-open");
document.querySelectorAll(".links a, .top-cta a").forEach((a) => {
  a.addEventListener("click", () => document.body.classList.remove("nav-open"));
});

const demo = document.getElementById("demo-form");
if (demo) {
  demo.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(demo).entries());
    const res = await fetch("/api/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const out = document.getElementById("demo-out");
    out.textContent = res.ok ? "Thanks — we'll reach out shortly." : "Could not send. Try again.";
  };
}
