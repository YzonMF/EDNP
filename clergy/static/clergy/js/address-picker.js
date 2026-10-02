/* Cascading Province -> City/Municipality -> Barangay picker for any input
 * or textarea marked up as:
 *   <input id="myField">
 *   <button type="button" class="ph-picker-btn" data-ph-picker-target="myField">...</button>
 * On completion it writes "Barangay, City, Province" into the target field —
 * any house/street/unit detail the user already typed is left in place, so
 * they can still edit the field by hand before or after using the picker.
 */
(function () {
  const SK = '<div class="ph-picker-loading" aria-label="Loading"><span class="sk-line w90"></span><span class="sk-line w70"></span><span class="sk-line w90"></span><span class="sk-line w50"></span></div>';
  const DATA_BASE = '/static/clergy/data/';

  let provincesPromise = null;
  let citiesPromise = null;
  const barangayCache = {};

  function loadProvinces() {
    if (!provincesPromise) {
      provincesPromise = fetch(DATA_BASE + 'provinces.json').then((r) => r.json());
    }
    return provincesPromise;
  }

  function loadCities() {
    if (!citiesPromise) {
      citiesPromise = fetch(DATA_BASE + 'cities.json').then((r) => r.json());
    }
    return citiesPromise;
  }

  function loadBarangays(cityCode) {
    if (!barangayCache[cityCode]) {
      barangayCache[cityCode] = fetch(DATA_BASE + 'barangays/' + cityCode + '.json')
        .then((r) => r.json())
        .catch(() => []);
    }
    return barangayCache[cityCode];
  }

  function closeAllPickers() {
    document.querySelectorAll('.ph-picker-panel').forEach((p) => p.remove());
  }

  function openPicker(triggerBtn, targetInput) {
    closeAllPickers();

    const panel = document.createElement('div');
    panel.className = 'ph-picker-panel';
    panel.innerHTML =
      '<div class="ph-picker-head">' +
        '<button type="button" class="ph-picker-back" style="display:none"><i class="fa-solid fa-arrow-left"></i></button>' +
        '<span class="ph-picker-title">Select Province</span>' +
        '<button type="button" class="ph-picker-close"><i class="fa-solid fa-xmark"></i></button>' +
      '</div>' +
      '<input type="text" class="ph-picker-search" placeholder="Search...">' +
      '<div class="ph-picker-list">' + SK + '</div>';
    triggerBtn.parentElement.appendChild(panel);

    const titleEl = panel.querySelector('.ph-picker-title');
    const backBtn = panel.querySelector('.ph-picker-back');
    const closeBtn = panel.querySelector('.ph-picker-close');
    const searchEl = panel.querySelector('.ph-picker-search');
    const listEl = panel.querySelector('.ph-picker-list');

    let stage = 'province';
    let selectedProvince = null;
    let selectedCity = null;
    let currentItems = [];
    let currentOnPick = null;

    function draw(items, onPick) {
      currentOnPick = onPick;
      if (!items.length) {
        listEl.innerHTML = '<div class="ph-picker-empty">No results.</div>';
        return;
      }
      listEl.innerHTML = '';
      items.forEach((item) => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'ph-picker-item';
        row.textContent = item.name;
        row.addEventListener('click', () => onPick(item));
        listEl.appendChild(row);
      });
    }

    function renderList(items, onPick) {
      currentItems = items;
      searchEl.value = '';
      draw(items, onPick);
      searchEl.focus();
    }

    searchEl.addEventListener('input', () => {
      const q = searchEl.value.trim().toLowerCase();
      const filtered = q ? currentItems.filter((i) => i.name.toLowerCase().includes(q)) : currentItems;
      draw(filtered, currentOnPick);
    });

    function showProvinces() {
      stage = 'province';
      titleEl.textContent = 'Select Province';
      backBtn.style.display = 'none';
      listEl.innerHTML = SK;
      loadProvinces().then((provinces) => renderList(provinces, pickProvince));
    }

    function setFieldValue(value) {
      targetInput.value = value;
      targetInput.dispatchEvent(new Event('input', { bubbles: true }));
      targetInput.dispatchEvent(new Event('change', { bubbles: true }));
    }

    function pickProvince(province) {
      selectedProvince = province;
      setFieldValue(province.name);
      showCities();
    }

    function showCities() {
      stage = 'city';
      titleEl.textContent = 'Select City / Municipality';
      backBtn.style.display = '';
      listEl.innerHTML = SK;
      loadCities().then((cities) => {
        const filtered = cities.filter((c) => c.province_code === selectedProvince.code);
        renderList(filtered, pickCity);
      });
    }

    function pickCity(city) {
      selectedCity = city;
      setFieldValue([city.name, selectedProvince.name].join(', '));
      showBarangays();
    }

    function showBarangays() {
      stage = 'barangay';
      titleEl.textContent = 'Select Barangay';
      backBtn.style.display = '';
      listEl.innerHTML = SK;
      loadBarangays(selectedCity.code).then((names) => {
        renderList(names.map((n) => ({ name: n })), pickBarangay);
      });
    }

    function pickBarangay(barangay) {
      setFieldValue([barangay.name, selectedCity.name, selectedProvince.name].join(', '));
      closeAllPickers();
    }

    backBtn.addEventListener('click', () => {
      if (stage === 'city') showProvinces();
      else if (stage === 'barangay') showCities();
    });
    closeBtn.addEventListener('click', closeAllPickers);

    showProvinces();

    setTimeout(() => {
      document.addEventListener('click', function onDocClick(e) {
        // Use composedPath() (fixed at dispatch time) rather than a live
        // panel.contains(e.target) check — picking a row replaces listEl's
        // contents synchronously, detaching e.target from the DOM before
        // this bubbles up here, which would otherwise make a live contains()
        // check see it as "outside" and close the picker on every pick.
        const path = e.composedPath ? e.composedPath() : [];
        if (path.includes(panel) || path.includes(triggerBtn)) return;
        panel.remove();
        document.removeEventListener('click', onDocClick);
      });
    }, 0);
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAllPickers();
  });

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-ph-picker-target]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const targetInput = document.getElementById(btn.getAttribute('data-ph-picker-target'));
        if (targetInput) openPicker(btn, targetInput);
      });
    });
  });
})();
