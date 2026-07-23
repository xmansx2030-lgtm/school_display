(function () {
  "use strict";

  const root = document.querySelector("[data-guide-root]");
  if (!root) return;

  const progress = Math.max(0, Math.min(100, Number(root.dataset.progress) || 0));
  const progressDial = root.querySelector("[data-guide-progress-dial]");
  if (progressDial) progressDial.style.setProperty("--progress", String(progress));

  const setupDetails = Array.from(root.querySelectorAll('[data-guide-group="setup"] > details'));
  const firstIncomplete = setupDetails.find((item) => item.dataset.complete === "false");
  if (firstIncomplete) firstIncomplete.open = true;

  const setSetupState = (isOpen) => setupDetails.forEach((item) => { item.open = isOpen; });
  root.querySelector("[data-guide-expand='setup']")?.addEventListener("click", () => setSetupState(true));
  root.querySelector("[data-guide-collapse='setup']")?.addEventListener("click", () => setSetupState(false));

  const search = root.querySelector("[data-guide-search]");
  const cards = Array.from(root.querySelectorAll("[data-guide-card]"));
  const filters = Array.from(root.querySelectorAll("[data-guide-filter]"));
  const count = root.querySelector("[data-guide-result-count]");
  const empty = root.querySelector("[data-guide-empty]");
  let activeCategory = "all";

  const normalizeArabic = (value) => String(value || "")
    .toLocaleLowerCase("ar")
    .normalize("NFKD")
    .replace(/[\u064B-\u065F\u0670]/g, "")
    .replace(/[أإآ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ة/g, "ه")
    .trim();

  const applyFilters = () => {
    const query = normalizeArabic(search?.value);
    let visible = 0;

    cards.forEach((card) => {
      const categoryMatch = activeCategory === "all" || card.dataset.category === activeCategory;
      const searchableText = `${card.dataset.search || ""} ${card.textContent || ""}`;
      const searchMatch = !query || normalizeArabic(searchableText).includes(query);
      const show = categoryMatch && searchMatch;
      card.hidden = !show;
      if (show) visible += 1;
    });

    if (count) count.textContent = visible === 1 ? "شرح واحد" : `${visible} شروحات`;
    if (empty) empty.hidden = visible !== 0;
  };

  filters.forEach((button) => {
    button.addEventListener("click", () => {
      activeCategory = button.dataset.guideFilter || "all";
      filters.forEach((item) => {
        const selected = item === button;
        item.classList.toggle("is-active", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
      applyFilters();
    });
  });

  search?.addEventListener("input", applyFilters);

  root.querySelector("[data-guide-reset]")?.addEventListener("click", () => {
    if (search) search.value = "";
    activeCategory = "all";
    filters.forEach((item) => {
      const selected = item.dataset.guideFilter === "all";
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    applyFilters();
    search?.focus();
  });

  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable;
    if (event.key === "/" && !isTyping && search) {
      event.preventDefault();
      search.focus();
    }
    if (event.key === "Escape" && document.activeElement === search && search?.value) {
      search.value = "";
      applyFilters();
    }
  });

  const navLinks = Array.from(root.querySelectorAll("[data-guide-nav]"));
  const sections = navLinks
    .map((link) => document.getElementById(link.dataset.guideNav))
    .filter(Boolean);

  if ("IntersectionObserver" in window && sections.length) {
    const sectionObserver = new IntersectionObserver((entries) => {
      const visibleEntries = entries.filter((entry) => entry.isIntersecting);
      if (!visibleEntries.length) return;
      const current = visibleEntries.sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      navLinks.forEach((link) => link.classList.toggle("is-active", link.dataset.guideNav === current.target.id));
    }, { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.15, 0.4] });
    sections.forEach((section) => sectionObserver.observe(section));
  }

  const readingBar = root.querySelector("[data-guide-reading-progress]");
  let ticking = false;
  const updateReadingProgress = () => {
    const rootRect = root.getBoundingClientRect();
    const available = Math.max(1, root.scrollHeight - window.innerHeight);
    const completed = Math.max(0, Math.min(1, -rootRect.top / available));
    if (readingBar) readingBar.style.width = `${completed * 100}%`;
    ticking = false;
  };

  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(updateReadingProgress);
  }, { passive: true });
  updateReadingProgress();
})();
