# lazyway-io-design (always-on rule)

This project uses the **lazyway-io-design** system (https://github.com/jpbaking/lazyway-io-design). For ANY page or UI work (HTML, React, components, styling, charts, favicons):

1. Load the `lazyway-io-design` skill first (`/lazyway-io-design`) and follow it literally. If the skill is not installed, clone the repo above (`git clone --depth 1`) and follow its `CHEATSHEET.md` instead.
2. Never write custom CSS when a documented class exists; never hardcode a color, px size, or shadow — tokens (`var(--…)`) only.
3. Exactly one amber accent per page. No gradients. Uppercase mono for labels only.
4. Every page ships the standard favicon links, `/design/styles.css`, and `/design/components.css` (copy `design/` from the repo if missing — never recreate it by hand).
