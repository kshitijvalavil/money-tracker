// "We are updating" page. Shows a friendly message, asks the server every few
// seconds whether it is ready, and opens the app as soon as it is.
(function () {
  var MESSAGES = [
    "Sherlock Bugs is inspecting every handoff with a very large magnifying glass.",
    "Tabledore is counting every rupee. Twice. For luck.",
    "Byte Vader is tightening the bolts on the vault.",
    "Sir Pixelot is polishing the buttons until you can see your face in them.",
    "Captain Shipit is checking the landing lights. All green so far.",
    "Your money is staying exactly where it is. Nothing is being moved."
  ];
  var POLL_MS = 3000, TALK_MS = 4000;
  var card = document.getElementById("card");
  var chatter = document.getElementById("chatter");
  var i = 0, done = false;

  var talk = setInterval(function () {
    if (done) return;
    i = (i + 1) % MESSAGES.length;
    chatter.textContent = MESSAGES[i];
  }, TALK_MS);

  function landed() {
    if (done) return;
    done = true;
    clearInterval(talk);
    card.setAttribute("data-state", "landed");
    document.getElementById("kicker").textContent = "All done";
    document.getElementById("title").textContent = "Touchdown! The new build has landed";
    document.getElementById("lead").textContent = "Everything is ready and your money is right where you left it. Taking you back in.";
    chatter.textContent = "Captain Shipit says: smooth landing. You can start using the app now.";
    var open = document.getElementById("open");
    open.hidden = false;
    document.getElementById("small").hidden = true;
    // Back to where they were going. If they opened this page directly, go home.
    setTimeout(function () {
      if (location.pathname === "/deploying") location.replace("/");
      else location.reload();
    }, 2500);
  }

  function check() {
    if (done) return;
    fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s && s.ok && !s.deploying) landed(); })
      .catch(function () { /* still updating: the server is not answering yet */ });
  }
  setInterval(check, POLL_MS);
  check();
})();
