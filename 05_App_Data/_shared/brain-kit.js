/**
 * Brain Kit — Lightweight helpers wrapping window.brain
 *
 * Usage in any app:
 *   <script src="/file/05_App_Data/_shared/brain-kit.js"></script>
 *
 * Provides:
 *   - BrainStore: JSON read/write with namespace, error handling, and defaults
 *   - toast(): Show/hide toast notifications
 *   - modal(): Promise-based modal dialogs
 *   - tabs(): Tab switching with page visibility management
 *
 * All helpers use CSS classes from theme.css — include theme.css first.
 */

/* global window, document */

(function () {
  'use strict';

  // Guard: wait for window.brain (injected by bridge)
  if (!window.brain) {
    console.warn('[brain-kit] window.brain not found — helpers will be limited');
  }

  /* ==========================================================
     BrainStore — namespaced JSON persistence
     ========================================================== */

  /**
   * Create a store scoped to a data directory.
   *
   *   const store = BrainKit.store('hypertrophy');
   *   const data = await store.read('mesocycles.json', []);
   *   data.push(newMeso);
   *   await store.write('mesocycles.json', data);
   *   const files = await store.list();
   *   await store.remove('old-data.json');
   */
  function createStore(namespace) {
    var prefix = namespace ? namespace + '/' : '';

    return {
      /** Read a JSON file. Returns `fallback` on missing/corrupt file. */
      read: async function (filename, fallback) {
        if (fallback === undefined) fallback = null;
        try {
          var raw = await window.brain.readFile(prefix + filename);
          if (!raw || raw.trim() === '') return fallback;
          return JSON.parse(raw);
        } catch (e) {
          console.error('[BrainStore] read error:', prefix + filename, e);
          return fallback;
        }
      },

      /** Write a value as pretty-printed JSON. */
      write: async function (filename, data) {
        try {
          await window.brain.writeFile(
            prefix + filename,
            JSON.stringify(data, null, 2)
          );
        } catch (e) {
          console.error('[BrainStore] write error:', prefix + filename, e);
          BrainKit.toast('Failed to save ' + filename, 'error');
          throw e;
        }
      },

      /** List files in the namespace directory. Requires Bridge v2. */
      list: async function () {
        try {
          return await window.brain.listFiles(prefix.replace(/\/$/, ''));
        } catch (e) {
          console.error('[BrainStore] list error:', prefix, e);
          return [];
        }
      },

      /** Delete a file. Requires Bridge v2. */
      remove: async function (filename) {
        try {
          await window.brain.deleteFile(prefix + filename);
        } catch (e) {
          console.error('[BrainStore] remove error:', prefix + filename, e);
          throw e;
        }
      },

      /** Read multiple files in parallel. Returns object keyed by filename. */
      readAll: async function (filenames, fallback) {
        if (fallback === undefined) fallback = null;
        var results = {};
        var promises = filenames.map(function (f) {
          return createStore(namespace).read(f, fallback).then(function (data) {
            results[f] = data;
          });
        });
        await Promise.all(promises);
        return results;
      }
    };
  }


  /* ==========================================================
     Toast — auto-dismissing notification
     ========================================================== */

  var _toastEl = null;
  var _toastTimer = null;

  /**
   * Show a toast notification.
   *
   *   BrainKit.toast('Saved!', 'success');
   *   BrainKit.toast('Oops', 'error', 4000);
   *
   * @param {string} message  - Text to display
   * @param {string} [type]   - 'success' | 'error' | 'warning' | '' (default)
   * @param {number} [ms]     - Auto-hide delay in ms (default 2500)
   */
  function toast(message, type, ms) {
    if (!_toastEl) {
      _toastEl = document.createElement('div');
      _toastEl.className = 'toast';
      document.body.appendChild(_toastEl);
    }

    // Clear previous
    clearTimeout(_toastTimer);
    _toastEl.classList.remove('show', 'error', 'success', 'warning');

    _toastEl.textContent = message;
    if (type) _toastEl.classList.add(type);

    // Trigger reflow then show
    void _toastEl.offsetHeight;
    _toastEl.classList.add('show');

    _toastTimer = setTimeout(function () {
      _toastEl.classList.remove('show');
    }, ms || 2500);
  }


  /* ==========================================================
     Modal — promise-based dialog
     ========================================================== */

  /**
   * Show a modal with options and return the selected value.
   *
   *   const choice = await BrainKit.modal({
   *     title: 'Choose set type',
   *     subtitle: 'How do you want to perform this set?',
   *     options: [
   *       { label: 'Straight sets', value: 'straight' },
   *       { label: 'Myo-rep',       value: 'myorep' },
   *     ]
   *   });
   *   // choice === 'straight' | 'myorep' | null (dismissed)
   *
   * @param {Object} config
   * @param {string}   config.title
   * @param {string}   [config.subtitle]
   * @param {Array}    config.options - [{ label, value }]
   * @returns {Promise<*>} The selected option's value, or null if dismissed.
   */
  function modal(config) {
    return new Promise(function (resolve) {
      // Overlay
      var overlay = document.createElement('div');
      overlay.className = 'modal-overlay';

      // Modal
      var box = document.createElement('div');
      box.className = 'modal';

      // Title
      var title = document.createElement('div');
      title.className = 'modal-title';
      title.textContent = config.title || '';
      box.appendChild(title);

      // Subtitle
      if (config.subtitle) {
        var sub = document.createElement('div');
        sub.className = 'modal-subtitle';
        sub.style.marginBottom = 'var(--sp-lg)';
        sub.textContent = config.subtitle;
        box.appendChild(sub);
      }

      // Options
      var optWrap = document.createElement('div');
      optWrap.style.display = 'flex';
      optWrap.style.flexDirection = 'column';
      optWrap.style.gap = 'var(--sp-sm)';
      optWrap.style.marginTop = 'var(--sp-lg)';

      function dismiss(val) {
        overlay.classList.remove('show');
        setTimeout(function () {
          overlay.remove();
        }, 250);
        resolve(val);
      }

      (config.options || []).forEach(function (opt) {
        var btn = document.createElement('button');
        btn.className = 'btn btn-secondary';
        btn.style.width = '100%';
        btn.textContent = opt.label;
        btn.addEventListener('click', function () {
          dismiss(opt.value);
        });
        optWrap.appendChild(btn);
      });

      box.appendChild(optWrap);
      overlay.appendChild(box);

      // Click backdrop to dismiss
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) dismiss(null);
      });

      document.body.appendChild(overlay);

      // Animate in
      requestAnimationFrame(function () {
        overlay.classList.add('show');
      });
    });
  }


  /* ==========================================================
     Tabs — page switching helper
     ========================================================== */

  /**
   * Initialize tab switching. Looks for a tab bar and page containers.
   *
   *   BrainKit.tabs({
   *     bar: '#tab-bar',           // selector for the tab bar container
   *     pages: '.tab-page',        // selector for page elements (needs data-tab="id")
   *     active: 'dashboard',       // initial tab id
   *     onSwitch: (id) => {}       // callback after switching
   *   });
   *
   * Tab buttons inside the bar need `data-tab="tabId"`.
   * Page containers need matching `data-tab="tabId"`.
   *
   * @param {Object} config
   * @returns {{ switchTo: function(tabId) }} Controller for programmatic switching.
   */
  function tabs(config) {
    var bar = document.querySelector(config.bar);
    var pages = document.querySelectorAll(config.pages);
    var buttons = bar ? bar.querySelectorAll('[data-tab]') : [];
    var currentTab = config.active || null;

    function switchTo(tabId) {
      currentTab = tabId;

      buttons.forEach(function (btn) {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
      });

      pages.forEach(function (page) {
        page.style.display = page.getAttribute('data-tab') === tabId ? '' : 'none';
      });

      if (config.onSwitch) config.onSwitch(tabId);
    }

    // Bind clicks
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        switchTo(btn.getAttribute('data-tab'));
      });
    });

    // Initial state
    if (currentTab) switchTo(currentTab);

    return { switchTo: switchTo };
  }


  /* ==========================================================
     askClaude — convenience wrapper
     ========================================================== */

  /**
   * Ask Claude and get a parsed JSON response, with error handling.
   *
   *   const result = await BrainKit.askClaude('Estimate calories: chicken 6oz', { json: true });
   *
   * @param {string} prompt
   * @param {Object} [opts]
   * @param {boolean} [opts.json] - If true, parse response as JSON
   * @returns {Promise<string|Object>}
   */
  async function askClaude(prompt, opts) {
    if (!window.brain || !window.brain.askClaude) {
      throw new Error('askClaude requires Brain Bridge v2');
    }
    var response = await window.brain.askClaude(prompt, opts);
    if (opts && opts.json) {
      // Strip markdown fences if present
      var cleaned = response.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '').trim();
      return JSON.parse(cleaned);
    }
    return response;
  }


  /* ==========================================================
     Router — lightweight hash-based page router
     ========================================================== */

  /**
   * Create a hash router for multi-page navigation inside blob URL iframes.
   *
   *   const router = BrainKit.router({
   *     routes: {
   *       '':         renderHome,       // default route
   *       'settings': renderSettings,
   *       'item/:id': renderItem        // :id captured as param
   *     },
   *     el: '#app'                      // container selector (default: '#app')
   *   });
   *
   *   router.navigate('settings');
   *   router.navigate('item/42');
   *
   * Route handlers receive ({ params, el }) where:
   *   - params: object of captured :param values
   *   - el: the container DOM element
   *
   * @param {Object} config
   * @param {Object}  config.routes   - { pattern: handler(ctx) }
   * @param {string}  [config.el]     - Container selector (default '#app')
   * @param {Function} [config.onNotFound] - Called when no route matches
   * @returns {{ navigate: function(path), current: function() }}
   */
  function router(config) {
    var container = document.querySelector(config.el || '#app');
    var routes = config.routes || {};
    var compiledRoutes = [];

    // Compile route patterns into regexes
    Object.keys(routes).forEach(function (pattern) {
      var paramNames = [];
      var regexStr = '^' + pattern.replace(/:([^/]+)/g, function (_, name) {
        paramNames.push(name);
        return '([^/]+)';
      }) + '$';
      compiledRoutes.push({
        regex: new RegExp(regexStr),
        paramNames: paramNames,
        handler: routes[pattern]
      });
    });

    function resolve() {
      var hash = (window.location.hash || '#').slice(1);
      // Remove leading slash if present
      if (hash.charAt(0) === '/') hash = hash.slice(1);

      for (var i = 0; i < compiledRoutes.length; i++) {
        var route = compiledRoutes[i];
        var match = hash.match(route.regex);
        if (match) {
          var params = {};
          route.paramNames.forEach(function (name, idx) {
            params[name] = decodeURIComponent(match[idx + 1]);
          });
          route.handler({ params: params, el: container });
          return;
        }
      }

      // No match — try onNotFound or default route
      if (config.onNotFound) {
        config.onNotFound({ path: hash, el: container });
      } else if (routes['']) {
        routes['']({ params: {}, el: container });
      }
    }

    // Listen for hash changes
    window.addEventListener('hashchange', resolve);

    // Initial resolve
    resolve();

    return {
      navigate: function (path) {
        window.location.hash = '#' + path;
      },
      current: function () {
        return (window.location.hash || '#').slice(1).replace(/^\//, '');
      }
    };
  }


  /* ==========================================================
     Export
     ========================================================== */

  window.BrainKit = {
    store: createStore,
    toast: toast,
    modal: modal,
    tabs: tabs,
    askClaude: askClaude,
    router: router,
    version: '1.1.0'
  };

  console.log('[brain-kit v1.1.0] Loaded — BrainKit.store, .toast, .modal, .tabs, .askClaude, .router available');
})();
