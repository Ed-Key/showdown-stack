// Expandable-card mount + toggle helper. Used by Stages 1-5 to add
// inline-expandable cards (Damage matrix, Threats, Why, Annotation) under
// the pinned Recommendation section.

export type CardId = 'matrix' | 'threats' | 'explainer' | 'annotation';

export type Card = {
  id: CardId;
  title: string;
  body: HTMLElement;
  isExpanded: boolean;
  toggleBtn: HTMLElement;
};

export function mountExpandableCard(
  parent: HTMLElement,
  id: CardId,
  title: string,
): Card {
  const wrapper = document.createElement('div');
  wrapper.className = 'sc-card';
  wrapper.dataset.cardId = id;
  wrapper.innerHTML = `
    <div class="sc-card-header">
      <span class="sc-card-title">${title}</span>
      <span class="sc-card-toggle">[show]</span>
    </div>
    <div class="sc-card-body" style="display:none"></div>
  `;
  const toggleBtn = wrapper.querySelector<HTMLElement>('.sc-card-header')!;
  const body = wrapper.querySelector<HTMLElement>('.sc-card-body')!;
  const toggleLabel = wrapper.querySelector<HTMLElement>('.sc-card-toggle')!;
  const card: Card = { id, title, body, isExpanded: false, toggleBtn };
  toggleBtn.addEventListener('click', () => {
    card.isExpanded = !card.isExpanded;
    body.style.display = card.isExpanded ? 'block' : 'none';
    toggleLabel.textContent = card.isExpanded ? '[hide]' : '[show]';
  });
  parent.appendChild(wrapper);
  return card;
}
