/* Panel koordynatora (UX-PANEL-04): paleta poleceń i „ostatnio używane”.

   Ulepszenie progresywne – bez tego pliku menu, wyszukiwarka i przypinanie działają tak samo (formularze).
   Plik statyczny ładowany z ``nonce`` (CSP bez inline JS); napisy przychodzą z szablonu (atrybuty ``data-*``
   i ``<template id="kp-palette-template">``), bo tylko serwer zna język oglądającego.

   1. Ostatnio używane (D10): przy wejściu na ekran z pozycją menu zapisuje ją w ``localStorage`` (klucz
      z kontem i konkursem, 6 pozycji) i rysuje listę nad sekcjami menu. Pamięć przeglądarki bywa niedostępna
      (tryb prywatny, polityka firmowa) – wtedy lista po prostu się nie pojawia.
   2. Paleta (D6): Ctrl+K / ⌘K albo „/” poza polem tekstowym otwiera ``<dialog>`` z polem ``combobox``
      i listą ``listbox``: przypięte, ostatnio używane i wszystkie pozycje menu (filtr bez polskich znaków),
      a na końcu „Szukaj w panelu” – istniejąca wyszukiwarka ``/coordinator/search/?q=``. */
(function () {
  "use strict";

  var RECENT_LIMIT = 6;
  var RESULT_LIMIT = 60;
  var MIN_QUERY = 2;

  function normalize(text) {
    return (text || "")
      .toLowerCase()
      .replace(/ł/g, "l")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function svgIcon(name) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "kp-icon");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var use = document.createElementNS(ns, "use");
    use.setAttribute("href", "#kp-i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function samePage(href) {
    try {
      var url = new URL(href, window.location.href);
      return url.origin === window.location.origin && url.pathname + url.search === window.location.pathname + window.location.search;
    } catch (error) {
      return false;
    }
  }

  /* --- pozycje menu z DOM --------------------------------------------------------------------------- */

  function menuItems(nav) {
    var rows = [];
    var groups = nav.querySelectorAll(".panel-nav__group[data-nav-group]");
    Array.prototype.forEach.call(groups, function (group) {
      if (group.getAttribute("data-nav-group") === "konkursy-platformy") {
        return; // przełącznik konkursów prowadzi pod inne domeny – nie jest „ekranem tego panelu”
      }
      var heading = group.querySelector(".panel-nav__heading-label");
      var groupLabel = heading ? heading.textContent.trim() : "";
      var items = group.querySelectorAll(".panel-nav__item");
      Array.prototype.forEach.call(items, function (item) {
        var link = item.querySelector("a.panel-nav__link");
        if (!link) {
          return;
        }
        var labelNode = link.querySelector(".panel-nav__label");
        var label = (labelNode ? labelNode.textContent : link.textContent).trim();
        rows.push({ label: label, href: link.getAttribute("href"), group: groupLabel, icon: "arrow" });
        var children = item.querySelectorAll("a.panel-nav__sublink");
        Array.prototype.forEach.call(children, function (child) {
          rows.push({
            label: label + " › " + child.textContent.trim(),
            href: child.getAttribute("href"),
            group: groupLabel,
            icon: "arrow",
          });
        });
      });
    });
    return rows;
  }

  function pinnedItems(nav) {
    var rows = [];
    var links = nav.querySelectorAll("[data-kp-pins] a.kp-quick__link");
    Array.prototype.forEach.call(links, function (link) {
      rows.push({ label: link.textContent.trim(), href: link.getAttribute("href"), group: link.getAttribute("data-kp-group") || "", icon: "pin" });
    });
    return rows;
  }

  /* --- 1. ostatnio używane -------------------------------------------------------------------------- */

  function readRecent(key) {
    try {
      var parsed = JSON.parse(window.localStorage.getItem(key) || "[]");
      return Array.isArray(parsed) ? parsed.filter(function (row) { return row && row.href && row.label; }) : [];
    } catch (error) {
      return [];
    }
  }

  function writeRecent(key, rows) {
    try {
      window.localStorage.setItem(key, JSON.stringify(rows.slice(0, RECENT_LIMIT)));
    } catch (error) {
      /* brak pamięci przeglądarki – lista po prostu nie powstanie */
    }
  }

  function currentMenuItem(nav) {
    var candidates = nav.querySelectorAll(".panel-nav__group a[aria-current='page']");
    var best = null;
    Array.prototype.forEach.call(candidates, function (link) {
      var group = link.closest(".panel-nav__group");
      if (group && group.getAttribute("data-nav-group") === "konkursy-platformy") {
        return;
      }
      if (!best || link.classList.contains("panel-nav__sublink")) {
        best = link;
      }
    });
    if (!best) {
      return null;
    }
    var label = best.textContent.trim();
    if (best.classList.contains("panel-nav__sublink")) {
      var parent = best.closest(".panel-nav__item");
      var parentLabel = parent ? parent.querySelector(".panel-nav__label") : null;
      if (parentLabel) {
        label = parentLabel.textContent.trim() + " › " + label;
      }
    } else {
      var own = best.querySelector(".panel-nav__label");
      if (own) {
        label = own.textContent.trim();
      }
    }
    var heading = best.closest(".panel-nav__group").querySelector(".panel-nav__heading-label");
    return { href: best.getAttribute("href"), label: label, group: heading ? heading.textContent.trim() : "" };
  }

  function initRecent(nav) {
    var root = nav.closest("details.panel-nav") || nav.querySelector("details.panel-nav");
    var key = root ? root.getAttribute("data-kp-recent-key") : "";
    if (!key) {
      return [];
    }
    var rows = readRecent(key);
    var current = currentMenuItem(nav);
    if (current && current.group) {
      // Start (sekcja „Pulpit”) nie jest „ostatnio używany” – stoi zawsze na górze menu.
      var isStart = root.querySelector(".panel-nav__group[data-nav-group='pulpit'] a[aria-current='page']");
      if (!isStart) {
        rows = [current].concat(rows.filter(function (row) { return row.href !== current.href; }));
        writeRecent(key, rows);
      }
    }
    var known = {};
    menuItems(nav).forEach(function (row) { known[row.href] = true; });
    // Pozycja, której nie ma już w menu (flaga wyłączona, etap usunięty), nie jest pokazywana.
    rows = rows.filter(function (row) { return known[row.href]; });
    var box = nav.querySelector("[data-kp-recent]");
    var list = nav.querySelector("[data-kp-recent-list]");
    if (box && list) {
      var visible = rows.filter(function (row) { return !samePage(row.href); }).slice(0, RECENT_LIMIT - 1);
      visible.forEach(function (row) {
        var li = document.createElement("li");
        var a = document.createElement("a");
        a.className = "kp-quick__link";
        a.href = row.href;
        a.textContent = row.label;
        li.appendChild(a);
        list.appendChild(li);
      });
      box.hidden = visible.length === 0;
    }
    return rows;
  }

  /* --- 2. paleta poleceń ----------------------------------------------------------------------------- */

  function Palette(nav, recent) {
    this.nav = nav;
    this.recent = recent;
    this.dialog = null;
    this.options = [];
    this.active = -1;
    this.opener = null;
  }

  Palette.prototype.build = function () {
    var template = document.getElementById("kp-palette-template");
    if (!template || !template.content) {
      return false;
    }
    this.dialog = template.content.firstElementChild.cloneNode(true);
    document.body.appendChild(this.dialog);
    this.input = this.dialog.querySelector(".kp-palette__input");
    this.list = this.dialog.querySelector(".kp-palette__list");
    var self = this;
    this.input.addEventListener("input", function () { self.render(); });
    this.input.addEventListener("keydown", function (event) { self.onKey(event); });
    this.dialog.querySelector("[data-kp-close]").addEventListener("click", function () { self.close(); });
    this.dialog.addEventListener("close", function () {
      if (self.opener && document.contains(self.opener)) {
        self.opener.focus();
      }
    });
    // Kliknięcie w tło (poza oknem) zamyka paletę – jak Esc.
    this.dialog.addEventListener("click", function (event) {
      if (event.target === self.dialog) {
        self.close();
      }
    });
    this.list.addEventListener("click", function (event) {
      var option = event.target.closest("[role='option']");
      if (option) {
        self.go(Number(option.getAttribute("data-index")));
      }
    });
    return true;
  };

  Palette.prototype.open = function (opener) {
    if (!this.dialog && !this.build()) {
      return;
    }
    if (this.dialog.open) {
      this.input.focus();
      return;
    }
    this.opener = opener || document.activeElement;
    this.input.value = "";
    this.render();
    if (typeof this.dialog.showModal === "function") {
      this.dialog.showModal();
    } else {
      this.dialog.setAttribute("open", "");
    }
    this.input.focus();
  };

  Palette.prototype.close = function () {
    if (this.dialog && this.dialog.open) {
      if (typeof this.dialog.close === "function") {
        this.dialog.close();
      } else {
        this.dialog.removeAttribute("open");
      }
    }
  };

  Palette.prototype.sections = function (query) {
    var labels = this.dialog.dataset;
    var menu = menuItems(this.nav);
    var pinned = pinnedItems(this.nav);
    var recent = this.recent.map(function (row) { return { label: row.label, href: row.href, group: row.group, icon: "history" }; });
    if (!query) {
      var sections = [];
      if (pinned.length) {
        sections.push({ label: labels.labelPinned, rows: pinned });
      }
      if (recent.length) {
        sections.push({ label: labels.labelRecent, rows: recent.slice(0, RECENT_LIMIT) });
      }
      var byGroup = [];
      var index = {};
      menu.forEach(function (row) {
        if (!(row.group in index)) {
          index[row.group] = byGroup.length;
          byGroup.push({ label: row.group, rows: [] });
        }
        byGroup[index[row.group]].rows.push(row);
      });
      return sections.concat(byGroup);
    }
    var tokens = normalize(query).split(" ");
    var seen = {};
    var hits = [];
    pinned.concat(recent, menu).forEach(function (row) {
      if (seen[row.href]) {
        return;
      }
      var haystack = normalize(row.label + " " + row.group);
      if (tokens.every(function (token) { return haystack.indexOf(token) !== -1; })) {
        seen[row.href] = true;
        // Trafienie od początku podpisu wyżej niż w środku – „wia” ma dać „Wiadomości”, a nie „Ogłoszenia w Wiadomościach”.
        hits.push({ row: row, rank: normalize(row.label).indexOf(tokens[0]) === 0 ? 0 : 1 });
      }
    });
    hits.sort(function (a, b) { return a.rank - b.rank; });
    var result = [{ label: labels.labelResults, rows: hits.slice(0, RESULT_LIMIT).map(function (hit) { return hit.row; }) }];
    if (query.trim().length >= MIN_QUERY && labels.searchUrl) {
      var url = labels.searchUrl + "?q=" + encodeURIComponent(query.trim());
      result.push({ label: labels.labelSearch, rows: [{ label: labels.labelSearch + ": „" + query.trim() + "”", href: url, group: "", icon: "search" }] });
    }
    return result;
  };

  Palette.prototype.render = function () {
    var query = this.input.value;
    var sections = this.sections(query.trim());
    var list = this.list;
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    this.options = [];
    var self = this;
    sections.forEach(function (section, sectionIndex) {
      if (!section.rows.length) {
        return;
      }
      var group = document.createElement("li");
      group.setAttribute("role", "group");
      group.setAttribute("aria-labelledby", "kp-palette-group-" + sectionIndex);
      var heading = document.createElement("div");
      heading.className = "kp-palette__group";
      heading.id = "kp-palette-group-" + sectionIndex;
      heading.textContent = section.label;
      group.appendChild(heading);
      var inner = document.createElement("ul");
      inner.setAttribute("role", "none");
      inner.className = "kp-quick__list";
      section.rows.forEach(function (row) {
        var index = self.options.length;
        var option = document.createElement("li");
        option.setAttribute("role", "option");
        option.id = "kp-palette-option-" + index;
        option.className = "kp-palette__option";
        option.setAttribute("data-index", String(index));
        option.setAttribute("aria-selected", "false");
        option.appendChild(svgIcon(row.icon || "arrow"));
        var label = document.createElement("span");
        label.className = "kp-palette__option-label";
        label.textContent = row.label;
        option.appendChild(label);
        if (row.group && section.label !== row.group) {
          var meta = document.createElement("span");
          meta.className = "kp-palette__option-meta";
          meta.textContent = row.group;
          option.appendChild(meta);
        }
        inner.appendChild(option);
        self.options.push({ node: option, href: row.href });
      });
      group.appendChild(inner);
      list.appendChild(group);
    });
    this.select(this.options.length ? 0 : -1);
  };

  Palette.prototype.select = function (index) {
    if (this.active >= 0 && this.options[this.active]) {
      this.options[this.active].node.setAttribute("aria-selected", "false");
    }
    this.active = index;
    if (index >= 0 && this.options[index]) {
      var node = this.options[index].node;
      node.setAttribute("aria-selected", "true");
      this.input.setAttribute("aria-activedescendant", node.id);
      if (typeof node.scrollIntoView === "function") {
        node.scrollIntoView({ block: "nearest" });
      }
    } else {
      this.input.removeAttribute("aria-activedescendant");
    }
  };

  Palette.prototype.onKey = function (event) {
    var count = this.options.length;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (count) {
        this.select((this.active + 1) % count);
      }
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (count) {
        this.select((this.active - 1 + count) % count);
      }
    } else if (event.key === "Home" && event.ctrlKey) {
      event.preventDefault();
      this.select(count ? 0 : -1);
    } else if (event.key === "End" && event.ctrlKey) {
      event.preventDefault();
      this.select(count - 1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      this.go(this.active);
    }
  };

  Palette.prototype.go = function (index) {
    var option = this.options[index];
    if (option && option.href) {
      window.location.assign(option.href);
    }
  };

  function isTyping(target) {
    if (!target) {
      return false;
    }
    var tag = (target.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
  }

  function init() {
    var nav = document.querySelector(".panel--coordinator .panel__nav");
    if (!nav) {
      return;
    }
    var recent = initRecent(nav);
    var palette = new Palette(nav, recent);
    var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
    var buttons = document.querySelectorAll("[data-kp-palette-open]");
    Array.prototype.forEach.call(buttons, function (button) {
      var shortcut = button.querySelector("[data-kp-shortcut]");
      if (shortcut && mac) {
        shortcut.textContent = "⌘ K";
      }
      button.setAttribute("aria-keyshortcuts", mac ? "Meta+K" : "Control+K");
      button.hidden = false;
      button.addEventListener("click", function () { palette.open(button); });
    });
    document.addEventListener("keydown", function (event) {
      var key = (event.key || "").toLowerCase();
      if ((event.ctrlKey || event.metaKey) && !event.altKey && key === "k") {
        event.preventDefault();
        palette.open(document.activeElement);
      } else if (key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !isTyping(event.target)) {
        event.preventDefault();
        palette.open(document.activeElement);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
