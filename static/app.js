const dropzone = document.querySelector("#dropzone");
const input = document.querySelector("#pdf");
const label = document.querySelector("#drop-label");

if (dropzone && input && label) {
  const setFileLabel = () => {
    if (input.files.length > 0) {
      label.textContent = input.files[0].name;
    }
  };

  input.addEventListener("change", setFileLabel);

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      event.preventDefault();
      dropzone.classList.remove("is-dragging");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    if (event.dataTransfer.files.length > 0) {
      input.files = event.dataTransfer.files;
      setFileLabel();
    }
  });
}

const search = document.querySelector("#catalog-search");
const cards = Array.from(document.querySelectorAll(".subject-card"));
const categorySections = Array.from(document.querySelectorAll("[data-category-section]"));
const noResults = document.querySelector("#no-results");
const previewTitle = document.querySelector("#preview-title");
const previewFrame = document.querySelector("#preview-frame");
const previewDownload = document.querySelector("#preview-download");

const normalize = (value) => value.trim().toLowerCase();

if (search && cards.length > 0) {
  search.addEventListener("input", () => {
    const query = normalize(search.value);
    let visibleCount = 0;

    cards.forEach((card) => {
      const haystack = card.dataset.search || "";
      const matches = haystack.includes(query);
      card.classList.toggle("is-hidden", !matches);
      if (matches) {
        visibleCount += 1;
      }
    });

    if (noResults) {
      noResults.classList.toggle("hidden", visibleCount !== 0);
    }

    categorySections.forEach((section) => {
      const visibleCards = section.querySelectorAll(".subject-card:not(.is-hidden)").length;
      section.classList.toggle("is-hidden", visibleCards === 0);
      if (query) {
        section.classList.remove("is-collapsed");
      }
    });
  });
}

document.querySelectorAll("[data-category-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const section = button.closest("[data-category-section]");
    if (!section) {
      return;
    }
    const collapsed = section.classList.toggle("is-collapsed");
    button.setAttribute("aria-expanded", collapsed ? "false" : "true");
  });
});

document.querySelectorAll(".preview-button").forEach((button) => {
  button.addEventListener("click", () => {
    if (previewFrame && button.dataset.previewUrl) {
      previewFrame.src = button.dataset.previewUrl;
    }
    if (previewTitle && button.dataset.previewTitle) {
      previewTitle.textContent = button.dataset.previewTitle;
    }
    if (previewDownload && button.dataset.downloadUrl) {
      previewDownload.href = button.dataset.downloadUrl;
    }
  });
});
