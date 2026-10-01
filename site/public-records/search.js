/* Public Records client-side search (ADR 062 Phase 3). Filters
 * search-index.json entirely in the visitor's browser -- no server, no new
 * network call beyond the one same-origin fetch below. Progressive
 * enhancement: the markup this script controls ships `hidden`, so a visitor
 * with JavaScript disabled never sees an inert search box -- they get the
 * chronological list exactly as before. Vanilla JS, no dependency: at this
 * corpus size a hand-rolled substring filter needs no search library.
 */
(function () {
  "use strict";

  // Must stay equal to gen_findings_feed.SEARCH_INDEX_FILENAME /
  // check_publish_safety.SEARCH_INDEX_FILENAME -- three independent copies of
  // the same filename, no shared import between a Python script and this
  // static JS file. tests/test_search_js.py pins this constant against the
  // Python side so a rename on one without the others fails CI instead of
  // silently 404ing every page's fetch.
  var SEARCH_INDEX_FILENAME = "search-index.json";

  // Sentinel for "this field is absent on the entry" in a facet filter --
  // chosen to not collide with any real facility/type/severity value.
  var NOT_STATED = "__not_stated__";

  var container = document.getElementById("public-records-search");
  if (!container) {
    return;
  }
  // Reveal the search UI now that we know JS is actually running.
  container.hidden = false;

  var searchInput = document.getElementById("pr-search-q");
  var clearBtn = document.getElementById("pr-search-clear");
  var filterFacility = document.getElementById("pr-filter-facility");
  var filterType = document.getElementById("pr-filter-type");
  var filterSeverity = document.getElementById("pr-filter-severity");
  var dateMin = document.getElementById("pr-filter-date-min");
  var dateMax = document.getElementById("pr-filter-date-max");
  var statusEl = document.getElementById("pr-search-status");
  var searchResults = document.getElementById("pr-search-results");
  var browseList = document.getElementById("pr-browse-list");
  var browseNav = document.getElementById("pr-browse-nav");
  var filtersDetails = container.querySelector(".search-ui-filters");

  var indexData = null;
  var indexPromise = null;

  function setStatus(text) {
    statusEl.hidden = false;
    statusEl.textContent = text;
  }

  // Fetches search-index.json on first use (not on page load) and caches the
  // parsed array -- 46 pages share this script, so a visitor who never
  // searches never pays for the fetch. Same-origin, no credentials: this is
  // a public static file, never sent with cookies.
  function ensureData() {
    if (!indexPromise) {
      setStatus("Loading search index...");
      indexPromise = fetch(SEARCH_INDEX_FILENAME, { credentials: "omit" })
        .then(function (resp) {
          if (!resp.ok) {
            throw new Error("HTTP " + resp.status);
          }
          return resp.json();
        })
        .then(function (data) {
          if (!Array.isArray(data)) {
            throw new Error("unexpected search index shape");
          }
          indexData = data;
          populateFacets(data);
          return data;
        })
        .catch(function (err) {
          // Allow a retry on the next interaction rather than wedging the
          // search box in a permanently-failed state.
          indexPromise = null;
          browseList.hidden = false;
          browseNav.hidden = false;
          searchResults.hidden = true;
          clearBtn.hidden = true;
          setStatus("Search is temporarily unavailable. Browse the records below instead.");
          throw err;
        });
    }
    return indexPromise;
  }

  // Distinct values present for `field` across the dataset, sorted, with the
  // NOT_STATED sentinel appended last iff at least one entry omits the field
  // entirely -- values come from the data itself, never a hardcoded list, so
  // a facility/type/severity this corpus hasn't seen yet never silently
  // vanishes from the data and a blank one is never silently dropped either.
  function collect(data, field) {
    var seen = {};
    var hasMissing = false;
    data.forEach(function (entry) {
      var v = entry[field];
      if (v) {
        seen[v] = true;
      } else {
        hasMissing = true;
      }
    });
    var values = Object.keys(seen).sort(function (a, b) {
      return a.localeCompare(b);
    });
    if (hasMissing) {
      values.push(NOT_STATED);
    }
    return values;
  }

  function fillSelect(select, values) {
    values.forEach(function (v) {
      var opt = document.createElement("option");
      opt.value = v;
      opt.textContent = v === NOT_STATED ? "Not stated / other" : v;
      select.appendChild(opt);
    });
  }

  function populateFacets(data) {
    fillSelect(filterFacility, collect(data, "facility"));
    fillSelect(filterType, collect(data, "type"));
    fillSelect(filterSeverity, collect(data, "severity"));
  }

  function currentFilters() {
    return {
      q: searchInput.value.trim().toLowerCase(),
      facility: filterFacility.value,
      type: filterType.value,
      severity: filterSeverity.value,
      dateMin: dateMin.value,
      dateMax: dateMax.value
    };
  }

  function isActive(f) {
    return !!(f.q || f.facility || f.type || f.severity || f.dateMin || f.dateMax);
  }

  function matchesFacet(value, filterValue) {
    if (!filterValue) {
      return true;
    }
    if (filterValue === NOT_STATED) {
      return !value;
    }
    return value === filterValue;
  }

  function matchesText(entry, q) {
    if (!q) {
      return true;
    }
    var fields = [entry.title, entry.facility, entry.excerpt];
    for (var i = 0; i < fields.length; i++) {
      var v = fields[i];
      if (v && v.toLowerCase().indexOf(q) !== -1) {
        return true;
      }
    }
    return false;
  }

  // A hand-curated `date` can be "YYYY", "YYYY-MM" or blank, not just a full
  // "YYYY-MM-DD" (see findings_feed.parse_handcurated_rows). Parsed with a
  // regex + plain string comparison, never `new Date(...)` -- that would
  // apply the browser's local timezone to a date-only string and could shift
  // a day/month depending on where the visitor is. Returns [lower, upper]
  // bounds (both 'YYYY-MM-DD') for the real date a partial value could mean,
  // so a range filter can test for overlap instead of guessing a single day;
  // null for blank/unparseable, which the caller treats as "always matches"
  // rather than crashing or silently excluding the row.
  function parseDateBounds(str) {
    if (!str) {
      return null;
    }
    var m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(String(str).trim());
    if (!m) {
      return null;
    }
    var year = m[1];
    var month = m[2];
    var day = m[3];
    if (day) {
      var exact = year + "-" + month + "-" + day;
      return [exact, exact];
    }
    if (month) {
      var lastDay = new Date(Number(year), Number(month), 0).getDate();
      var lastDayStr = lastDay < 10 ? "0" + lastDay : String(lastDay);
      return [year + "-" + month + "-01", year + "-" + month + "-" + lastDayStr];
    }
    return [year + "-01-01", year + "-12-31"];
  }

  function matchesDateRange(entry, minStr, maxStr) {
    if (!minStr && !maxStr) {
      return true;
    }
    var bounds = parseDateBounds(entry.date);
    if (!bounds) {
      return true;
    }
    var lower = bounds[0];
    var upper = bounds[1];
    if (minStr && upper < minStr) {
      return false;
    }
    if (maxStr && lower > maxStr) {
      return false;
    }
    return true;
  }

  function showBrowseView() {
    browseList.hidden = false;
    browseNav.hidden = false;
    searchResults.hidden = true;
    statusEl.hidden = true;
  }

  function showSearchView(count, total) {
    browseList.hidden = true;
    browseNav.hidden = true;
    searchResults.hidden = false;
    setStatus("Showing " + count + " of " + total + " records.");
  }

  // Mirrors findings_feed.render_entry's structure (.finding / .finding-meta
  // / .finding-auto-label / .finding-kdp) so a search result and a
  // chronological entry are visually identical. Every field is written via
  // textContent/createElement, never interpolated as markup -- the index is
  // already curated/redacted (see findings_feed._public_view), but this
  // matches the Python side's own blanket _esc() discipline as defense in
  // depth, not because the data is expected to be hostile.
  function renderCard(entry) {
    var article = document.createElement("article");
    article.className = "finding";

    var metaBits = [];
    if (entry.date) {
      metaBits.push(entry.date);
    }
    if (entry.facility) {
      metaBits.push(entry.facility);
    }
    if (entry.type) {
      metaBits.push(entry.type);
    }
    if (entry.severity) {
      metaBits.push(entry.severity);
    }
    // "source" in entry (even "") marks a hand-curated row -- same
    // key-presence check findings_feed._search_entry uses, mirrored here so
    // a hand-curated result always shows a Source tag, same as the HTML.
    if ("source" in entry) {
      metaBits.push("Source: " + (entry.source || "not stated"));
    }
    if (metaBits.length) {
      var meta = document.createElement("p");
      meta.className = "finding-meta";
      meta.textContent = metaBits.join(" · ");
      article.appendChild(meta);
    }

    var h3 = document.createElement("h3");
    // Re-checked here, not just trusted from the JSON: an http(s)-only
    // scheme check, same rule findings_feed._public_view applies before a
    // link ever reaches this file. Anything else renders as plain text.
    var hasLink = typeof entry.link === "string" && /^https?:\/\//i.test(entry.link);
    if (hasLink) {
      var a = document.createElement("a");
      a.href = entry.link;
      a.textContent = entry.title || "(untitled document)";
      h3.appendChild(a);
    } else {
      h3.textContent = entry.title || "(untitled document)";
    }
    article.appendChild(h3);

    if (entry.excerpt) {
      // The index doesn't record whether this came from `summary` or
      // `key_data_point` (see findings_feed._search_entry), so this covers
      // both rather than picking render_entry's summary-only wording.
      // Deliberately avoids the word "excerpt" in this user-facing label --
      // that word denotes a verbatim passage lifted from the source, but the
      // text is Claude's own paraphrase (egle_doc_parser._classify_with_claude),
      // never literal document text. Same disclosure intent as render_entry
      // (insurance-readiness / master analysis 5.4: machine-generated text is
      // always labeled), worded to not imply a direct quotation.
      var label = document.createElement("p");
      label.className = "finding-auto-label";
      var sourceRef = hasLink ? "the linked document above" : "this document";
      label.textContent = "Automated summary or key data point from " + sourceRef +
        ". It is machine-generated and may contain errors. Consult the source " +
        "document before relying on it.";
      article.appendChild(label);

      var excerptP = document.createElement("p");
      excerptP.className = "finding-kdp";
      excerptP.textContent = entry.excerpt;
      article.appendChild(excerptP);
    }

    return article;
  }

  function renderResults(results) {
    while (searchResults.firstChild) {
      searchResults.removeChild(searchResults.firstChild);
    }
    if (!results.length) {
      var p = document.createElement("p");
      p.textContent = "No matching records.";
      searchResults.appendChild(p);
      return;
    }
    results.forEach(function (entry) {
      searchResults.appendChild(renderCard(entry));
    });
  }

  // Keeps the index's own order (newest-first, same as the chronological
  // list) rather than re-sorting results.
  function runFilter() {
    var f = currentFilters();
    var active = isActive(f);
    clearBtn.hidden = !active;
    if (!active) {
      showBrowseView();
      return;
    }
    if (!indexData) {
      // Defensive only: both current callers (the ensureData().then()
      // callback, and resetFilters() where `active` is already false above)
      // guarantee indexData is set by the time this line runs. Guards a
      // future caller that invokes runFilter() directly before data loads,
      // rather than crashing on indexData.filter(...) below.
      return;
    }
    var results = indexData.filter(function (entry) {
      return (
        matchesText(entry, f.q) &&
        matchesFacet(entry.facility, f.facility) &&
        matchesFacet(entry.type, f.type) &&
        matchesFacet(entry.severity, f.severity) &&
        matchesDateRange(entry, f.dateMin, f.dateMax)
      );
    });
    renderResults(results);
    showSearchView(results.length, indexData.length);
  }

  function onFilterChange() {
    ensureData().then(runFilter, function () {
      /* ensureData() already surfaced the error via setStatus(). */
    });
  }

  var debounceTimer = null;
  function onSearchInput() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(onFilterChange, 150);
  }

  function resetFilters() {
    clearTimeout(debounceTimer);
    searchInput.value = "";
    filterFacility.value = "";
    filterType.value = "";
    filterSeverity.value = "";
    dateMin.value = "";
    dateMax.value = "";
    runFilter();
  }

  searchInput.addEventListener("input", onSearchInput);
  filterFacility.addEventListener("change", onFilterChange);
  filterType.addEventListener("change", onFilterChange);
  filterSeverity.addEventListener("change", onFilterChange);
  dateMin.addEventListener("change", onFilterChange);
  dateMax.addEventListener("change", onFilterChange);
  clearBtn.addEventListener("click", resetFilters);
  // A <select> with only its default "All ..." option never fires "change"
  // on its own, so a visitor who opens Filters and clicks straight into a
  // facet dropdown (without first typing text or picking a date) would see
  // permanently empty-looking options with nothing left to trigger the
  // fetch that populates them. Loading on the panel's own "toggle" event
  // covers that path while keeping the fetch lazy (still not on page load).
  if (filtersDetails) {
    filtersDetails.addEventListener("toggle", onFilterChange);
  }
})();
