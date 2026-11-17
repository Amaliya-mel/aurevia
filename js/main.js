(() => {
  "use strict";

  const documentElement = document.documentElement;
  const body = document.body;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

  // Orchestrated hero entrance
  requestAnimationFrame(() => {
    requestAnimationFrame(() => body.classList.add("page-ready"));
  });

  // Header state and the optical waterline in the hero
  const header = document.querySelector("[data-header]");
  const waterline = document.querySelector("[data-waterline]");
  let scrollFrame = null;

  const updateScrollEffects = () => {
    const scrollTop = window.scrollY;
    header?.classList.toggle("is-scrolled", scrollTop > 40);

    if (waterline && !reducedMotion.matches) {
      const shift = clamp(scrollTop * 0.045, 0, 24);
      waterline.style.setProperty("--water-shift", `${shift}px`);
    }

    scrollFrame = null;
  };

  const requestScrollUpdate = () => {
    if (scrollFrame === null) {
      scrollFrame = requestAnimationFrame(updateScrollEffects);
    }
  };

  updateScrollEffects();
  window.addEventListener("scroll", requestScrollUpdate, { passive: true });

  // Accessible mobile navigation
  const menuToggle = document.querySelector("[data-menu-toggle]");
  const navigation = document.querySelector("[data-nav]");

  const setMenuState = (isOpen, returnFocus = false) => {
    if (!menuToggle || !navigation || !header) return;

    menuToggle.setAttribute("aria-expanded", String(isOpen));
    menuToggle.setAttribute(
      "aria-label",
      isOpen ? "Закрыть меню" : "Открыть меню",
    );
    navigation.classList.toggle("is-open", isOpen);
    header.classList.toggle("menu-is-open", isOpen);
    body.classList.toggle("menu-open", isOpen);

    if (isOpen) {
      navigation.querySelector("a")?.focus({ preventScroll: true });
    } else if (returnFocus) {
      menuToggle.focus({ preventScroll: true });
    }
  };

  menuToggle?.addEventListener("click", () => {
    const isOpen = menuToggle.getAttribute("aria-expanded") !== "true";
    setMenuState(isOpen);
  });

  navigation?.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMenuState(false);
  });

  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      menuToggle?.getAttribute("aria-expanded") === "true"
    ) {
      setMenuState(false, true);
    }
  });

  const desktopNavigation = window.matchMedia("(min-width: 64.0625rem)");
  desktopNavigation.addEventListener("change", (event) => {
    if (event.matches) setMenuState(false);
  });

  // Close the mobile menu when focus attempts to leave it via Tab.
  document.addEventListener("focusin", (event) => {
    if (
      menuToggle?.getAttribute("aria-expanded") === "true" &&
      navigation &&
      !navigation.contains(event.target) &&
      event.target !== menuToggle
    ) {
      navigation.querySelector("a")?.focus({ preventScroll: true });
    }
  });

  // Scroll-triggered content reveals
  const revealItems = [...document.querySelectorAll("[data-reveal]")];

  if (reducedMotion.matches || !("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
  } else {
    const revealObserver = new IntersectionObserver(
      (entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );

    revealItems.forEach((item) => revealObserver.observe(item));
  }

  // Testimonial carousel: deliberately manual, with keyboard and swipe support.
  const testimonialTrack = document.querySelector("[data-testimonial-track]");
  const testimonials = [...document.querySelectorAll("[data-testimonial]")];
  const previousTestimonial = document.querySelector("[data-testimonial-prev]");
  const nextTestimonial = document.querySelector("[data-testimonial-next]");
  const testimonialCounter = document.querySelector(
    "[data-testimonial-current]",
  );
  const testimonialViewport = testimonialTrack?.parentElement;
  let testimonialIndex = 0;
  let swipeStart = null;

  const showTestimonial = (newIndex) => {
    if (!testimonialTrack || testimonials.length === 0) return;

    testimonialIndex = (newIndex + testimonials.length) % testimonials.length;
    testimonialTrack.style.transform = `translate3d(-${testimonialIndex * 100}%, 0, 0)`;

    testimonials.forEach((testimonial, index) => {
      const isActive = index === testimonialIndex;
      testimonial.classList.toggle("is-active", isActive);
      testimonial.setAttribute("aria-hidden", String(!isActive));
    });

    if (testimonialCounter) {
      testimonialCounter.textContent = String(testimonialIndex + 1).padStart(
        2,
        "0",
      );
    }
  };

  previousTestimonial?.addEventListener("click", () =>
    showTestimonial(testimonialIndex - 1),
  );
  nextTestimonial?.addEventListener("click", () =>
    showTestimonial(testimonialIndex + 1),
  );

  if (testimonialViewport) {
    testimonialViewport.tabIndex = 0;
    testimonialViewport.setAttribute("role", "region");
    testimonialViewport.setAttribute("aria-label", "Отзывы клиентов");

    testimonialViewport.addEventListener("keydown", (event) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        showTestimonial(testimonialIndex - 1);
      }

      if (event.key === "ArrowRight") {
        event.preventDefault();
        showTestimonial(testimonialIndex + 1);
      }
    });

    testimonialViewport.addEventListener(
      "pointerdown",
      (event) => {
        swipeStart = event.clientX;
      },
      { passive: true },
    );

    testimonialViewport.addEventListener(
      "pointerup",
      (event) => {
        if (swipeStart === null) return;
        const distance = event.clientX - swipeStart;
        swipeStart = null;

        if (Math.abs(distance) < 45) return;
        showTestimonial(testimonialIndex + (distance < 0 ? 1 : -1));
      },
      { passive: true },
    );

    testimonialViewport.addEventListener("pointercancel", () => {
      swipeStart = null;
    });
  }

  // Contact form validation and submission
  const form = document.querySelector("[data-contact-form]");

  if (form) {
    const submitButton = form.querySelector("[data-submit-button]");
    const submitLabel = form.querySelector("[data-submit-label]");
    const formStatus = form.querySelector("[data-form-status]");
    const fields = {
      name: form.elements.name,
      contact: form.elements.contact,
      message: form.elements.message,
      consent: form.elements.consent,
    };

    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/i;
    const hasValidPhone = (value) => {
      const digits = value.replace(/\D/g, "");
      return digits.length >= 10 && digits.length <= 15;
    };

    const validators = {
      name: (value) => (value.trim().length >= 2 ? "" : "Укажите имя."),
      contact: (value) => {
        const normalized = value.trim();
        return emailPattern.test(normalized) || hasValidPhone(normalized)
          ? ""
          : "Введите корректный телефон или email.";
      },
      message: (value) =>
        value.trim().length >= 12
          ? ""
          : "Расскажите о проекте несколькими словами.",
      consent: (value) => (value ? "" : "Нужно согласие на обработку данных."),
    };

    const getFieldValue = (field) =>
      field.type === "checkbox" ? field.checked : field.value;

    const setFieldError = (name, message) => {
      const field = fields[name];
      const error = form.querySelector(`[data-error-for="${name}"]`);
      const wrapper = field.closest(".field, .form-consent");

      field.setAttribute("aria-invalid", String(Boolean(message)));
      wrapper?.classList.toggle("is-invalid", Boolean(message));
      if (error) error.textContent = message;
    };

    const validateField = (name) => {
      const field = fields[name];
      const message = validators[name](getFieldValue(field));
      setFieldError(name, message);
      return !message;
    };

    Object.entries(fields).forEach(([name, field]) => {
      const eventName = field.type === "checkbox" ? "change" : "blur";
      field.addEventListener(eventName, () => validateField(name));

      field.addEventListener(
        field.type === "checkbox" ? "change" : "input",
        () => {
          if (field.getAttribute("aria-invalid") === "true")
            validateField(name);
          if (formStatus?.textContent) formStatus.textContent = "";
          form.classList.remove("is-success");
        },
      );
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (form.classList.contains("is-sending")) return;

      const fieldNames = Object.keys(fields);
      const formIsValid = fieldNames.map(validateField).every(Boolean);

      if (!formIsValid) {
        const firstInvalid = form.querySelector('[aria-invalid="true"]');
        firstInvalid?.focus();
        if (formStatus) formStatus.textContent = "Проверьте отмеченные поля.";
        return;
      }

      form.classList.add("is-sending");
      form.classList.remove("is-success");
      if (submitButton) submitButton.disabled = true;
      if (submitLabel) submitLabel.textContent = "Отправляем…";
      if (formStatus) formStatus.textContent = "Передаём описание проекта…";

      try {
        const payload = Object.fromEntries(new FormData(form).entries());
        const response = await fetch(form.action, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(result.error || "Не удалось отправить заявку.");
        }
      } catch (error) {
        form.classList.remove("is-sending");
        if (submitButton) submitButton.disabled = false;
        if (submitLabel) submitLabel.textContent = "Попробовать снова";
        if (formStatus) {
          formStatus.textContent =
            error instanceof Error
              ? error.message
              : "Не удалось отправить заявку. Попробуйте ещё раз.";
        }
        return;
      }

      form.reset();
      fieldNames.forEach((name) => setFieldError(name, ""));
      form.classList.remove("is-sending");
      form.classList.add("is-success");
      if (submitButton) submitButton.disabled = false;
      if (submitLabel) submitLabel.textContent = "Заявка отправлена";
      if (formStatus) {
        formStatus.textContent =
          "Спасибо. Мы получили описание проекта и свяжемся с вами в ближайшее рабочее время.";
      }

      window.setTimeout(() => {
        if (submitLabel) submitLabel.textContent = "Обсудить проект";
      }, 5000);
    });
  }

  // Avoid leaving the document in a locked state after page restoration.
  window.addEventListener("pageshow", () => {
    if (menuToggle?.getAttribute("aria-expanded") !== "true") {
      body.classList.remove("menu-open");
    }
    documentElement.style.removeProperty("scroll-behavior");
  });
})();
