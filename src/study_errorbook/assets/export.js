/* Offline rendering only. Material is escaped text; no HTML or remote TeX extensions. */
(() => {
  const notice = document.querySelector('[data-math-notice]');
  if (typeof renderMathInElement !== 'function') return;
  let failed = false;
  for (const element of document.querySelectorAll('.rich-text')) {
    renderMathInElement(element, {
      delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '\\(', right: '\\)', display: false},
        {left: '\\[', right: '\\]', display: true}
      ],
      throwOnError: true,
      strict: 'error',
      trust: () => { failed = true; return false; },
      maxExpand: 1000,
      maxSize: 20,
      macros: {},
      errorCallback: () => { failed = true; }
    });
  }
  notice.textContent = failed ? '部分公式无法排版或包含不支持的命令，已保留原文；请核对后打印。' : '';
  notice.hidden = !failed;
  document.documentElement.dataset.mathReady = 'true';
})();
