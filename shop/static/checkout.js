"use strict";

// The session lives only in memory for this page; nothing is written to browser storage.
const form = document.getElementById("pay-form");
const status = document.getElementById("pay-status");

function show(message) {
  // textContent, never innerHTML: the message is text, not markup.
  status.textContent = message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const holder = document.getElementById("holder-name");
  if (!holder.value.trim()) {
    show("Please enter the name of the person paying.");
    holder.focus();
    return;
  }
  show("Processing your payment...");
  try {
    const response = await fetch(form.dataset.payUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${form.dataset.session}` },
      body: JSON.stringify({ payment_token: form.dataset.providerToken }),
    });
    show(response.ok ? "Thank you, your payment went through." : "The payment did not go through.");
  } catch (error) {
    show("We could not reach the shop. Please try again.");
  }
});
