const search = document.querySelector('#stock-search');
search.addEventListener('input', () => {
  const query = search.value.trim().toLowerCase();
  const items = [...document.querySelectorAll('.stock-detail')];
  items.forEach(item => { item.hidden = !item.querySelector('summary').textContent.toLowerCase().includes(query); });
  document.querySelector('#search-count').textContent = query ? `找到 ${items.filter(x => !x.hidden).length} 只` : '';
});
function revealTarget() {
  if (!location.hash) return;
  const item = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (!item) return;
  let parent = item;
  while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent.hidden = false; parent = parent.parentElement; }
}
window.addEventListener('hashchange', revealTarget);
document.querySelectorAll('.stock-link').forEach(link => link.addEventListener('click', () => {
  const item = document.querySelector(link.getAttribute('href'));
  if (item) { item.hidden = false; item.open = true; }
}));
revealTarget();
let printState = [];
window.addEventListener('beforeprint', () => {
  printState = [...document.querySelectorAll('details')].map(x => [x, x.open, x.hidden]);
  printState.forEach(([x]) => { x.open = true; x.hidden = false; });
});
window.addEventListener('afterprint', () => printState.forEach(([x, open, hidden]) => { x.open = open; x.hidden = hidden; }));
document.querySelector('#print-report').addEventListener('click', () => window.print());
if ('IntersectionObserver' in window) {
const observer = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) {
    document.querySelectorAll('.toc a').forEach(a => a.classList.toggle('active', a.hash === '#' + entry.target.id));
  }
}, {rootMargin: '-10% 0px -65% 0px'});
document.querySelectorAll('main>section').forEach(section => observer.observe(section));
}
