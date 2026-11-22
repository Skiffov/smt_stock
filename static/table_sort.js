/* Column-header sorting for the rows already loaded on the page.
   Preserves search/filter GET parameters and does not modify the database. */
(() => {
  'use strict';
  const collator = new Intl.Collator('uk-UA', { numeric: true, sensitivity: 'base' });
  const numberHeaders = new Set([
    'кількість', 'придатний залишок', 'у блок-зоні', 'упаковок',
    'видано', 'повернуто', 'чиста видача', 'проєктів', 'операцій',
    'позицій bom', 'без видач', 'чиста видача понад план', 'менша за план',
    'план bom', 'заявка', 'id', 'план', 'отримано'
  ]);
  const dateHeaders = new Set(['час', 'створено', 'дата', 'оновлено', 'хто й коли']);
  const actionHeaders = new Set(['дія', 'дії']);

  function normalizedText(cell) {
    // Ignore buttons/forms within operational cells, and prefer an explicit key if provided.
    return (cell.getAttribute('data-sort-value') ?? cell.innerText ?? cell.textContent ?? '').trim().replace(/\s+/g, ' ');
  }
  function numericValue(str) {
    const m = String(str).replace(/\u00a0/g, ' ').match(/[-+]?\d[\d\s]*(?:[.,]\d+)?/);
    return m ? Number(m[0].replace(/\s/g, '').replace(',', '.')) : NaN;
  }
  function dateValue(str) {
    const iso = str.match(/\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?/);
    if (iso) return Date.parse(iso[0].replace(' ', 'T'));
    const ua = str.match(/(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?/);
    return ua ? new Date(+ua[3], +ua[2]-1, +ua[1], +(ua[4] || 0), +(ua[5] || 0)).getTime() : NaN;
  }
  function parseKey(raw, type) {
    if (type === 'number') return numericValue(raw);
    if (type === 'date') return dateValue(raw);
    return raw;
  }
  function compare(a, b, type) {
    const emptyA = a === '' || (typeof a === 'number' && !Number.isFinite(a));
    const emptyB = b === '' || (typeof b === 'number' && !Number.isFinite(b));
    if (emptyA || emptyB) return (emptyA ? 1 : 0) - (emptyB ? 1 : 0);
    if (type === 'number' || type === 'date') return a-b;
    return collator.compare(String(a), String(b));
  }

  document.querySelectorAll('.table-wrap > table').forEach((table) => {
    const heading = table.tHead?.rows[0] ?? table.rows[0];
    if (!heading || !heading.cells.length || !heading.querySelector('th')) return;
    const body = table.tBodies[0];
    if (!body) return;
    const records = Array.from(body.rows).filter(row => row !== heading && row.cells.length === heading.cells.length && !Array.from(row.cells).some(cell => cell.colSpan > 1));
    if (records.length === 0) return;
    table.classList.add('sortable-table');

    Array.from(heading.cells).forEach((th, col) => {
      const title = th.textContent.trim();
      if (!title || actionHeaders.has(title.toLocaleLowerCase('uk-UA')) || th.querySelector('button, a, input, select')) return;
      const key = title.toLocaleLowerCase('uk-UA');
      const type = numberHeaders.has(key) ? 'number' : dateHeaders.has(key) ? 'date' : 'text';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sort-header';
      btn.setAttribute('aria-label', `Сортувати за колонкою «${title}»`);
      const name = document.createElement('span');
      name.textContent = title;
      const arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      arrow.setAttribute('aria-hidden', 'true');
      arrow.textContent = '↕';
      btn.append(name, arrow);
      th.replaceChildren(btn);
      th.setAttribute('aria-sort', 'none');
      btn.addEventListener('click', () => {
        const ascending = th.getAttribute('aria-sort') !== 'ascending';
        heading.querySelectorAll('th').forEach(other => {
          other.setAttribute('aria-sort', 'none');
          const a = other.querySelector('.sort-arrow');
          if (a) a.textContent = '↕';
        });
        th.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
        arrow.textContent = ascending ? '↑' : '↓';
        const sorted = records.map((row, index) => ({row,index,key:parseKey(normalizedText(row.cells[col]),type)}));
        sorted.sort((a,b) => {
          const order = compare(a.key,b.key,type);
          return (ascending ? order : -order) || (a.index - b.index);
        });
        sorted.forEach(({row}) => body.appendChild(row));
      });
    });
  });
})();
