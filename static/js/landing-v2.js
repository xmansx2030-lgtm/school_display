document.addEventListener('DOMContentLoaded', () => {
    const showcase = document.querySelector('[data-product-showcase]');
    if (!showcase) return;

    const tabs = Array.from(showcase.querySelectorAll('[data-shot-target]'));
    const shots = Array.from(showcase.querySelectorAll('[data-shot]'));

    tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.shotTarget;

            tabs.forEach((item) => {
                const selected = item === tab;
                item.classList.toggle('active', selected);
                item.setAttribute('aria-selected', selected ? 'true' : 'false');
            });

            shots.forEach((shot) => {
                const selected = shot.dataset.shot === target;
                shot.hidden = !selected;
                shot.classList.toggle('active', selected);
            });
        });
    });
});
