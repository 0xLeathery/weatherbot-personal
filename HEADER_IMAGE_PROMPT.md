# Header image prompt for Nano Banana

Aspect ratio: **5:2** (e.g. 1500 × 600 px). For X long-form Articles, the header sits above the title — should read at small sizes too.

## Primary prompt (recommended)

> A wide cinematic 5:2 banner image for a technical article about a Polymarket weather trading bot. Centered composition: a stylized financial trading terminal screen with dark teal-charcoal background (oklch-style: near-black with cool blue undertones, like #0d1117 to #1a2332), showing a glowing cyan equity-curve line chart on the left half. The chart has a faint mint-green gradient fill underneath the line and a dashed horizontal "starting balance" reference line. On the right half: a stylized weather radar overlay — concentric soft-cyan rings with small pinpoint dots representing 8–12 cities scattered across a faint world-map silhouette. Tasteful monospace text fragments visible in the chart area like "EV +5.9x", "KORD 46–47°F", "σ 2.0", "BUY $20.00" rendered in JetBrains Mono style, slightly out of focus so they read as texture not specifics. Overall mood: serious, analytical, trader-terminal aesthetic — not crypto-glitzy, not finance-suit. Soft volumetric light from the upper left, very slight scan-line texture, no humans, no logos, no specific brand marks. Color palette: charcoal (#1a1f2e), deep cyan (#5ad6ff), signal green (#7dd97d), warm amber accent (#f5b94a) used sparingly. Composition leaves the central horizontal third quieter so a title can be overlaid in post if needed. Photorealistic UI mockup style, 4K detail, sharp typography, NOT cartoonish, NOT meme art.

## Variations to try if the first doesn't land

**Variation A — more weather, less terminal:**
> Same 5:2 banner, but shift weight: 40% trading terminal on right, 60% atmospheric weather imagery on left. Soft cumulus cloud formations rendered in muted cyan-grey, with subtle isobar contour lines overlaid like a meteorological forecast map. The trading terminal portion shows just one bold KPI tile reading "+2.4%" in signal-green and a small sparkline. Same color palette. Cinematic, magazine-cover quality.

**Variation B — minimalist conceptual:**
> Wide 5:2 minimalist conceptual banner. Dark charcoal background. Center: a single large temperature gauge or thermometer rendered in clean line-art (cyan strokes), with its mercury column replaced by an upward-trending stock chart line. To one side, faint floating numerical fragments suggesting probability and EV ("0.145 → 0.30", "p=0.78", "EV +5.9x") in monospace. Lots of negative space. Editorial, restrained, looks like it could be on the cover of an MIT Tech Review piece about prediction markets.

**Variation C — receipt/audit angle:**
> 5:2 banner with a "forensic audit" feel. Dark background. Layered translucent panels showing fragments of: a Python code block with `def scan_and_update():` visible, a JSON snippet with `"calibration": {...}`, and a weather forecast chart with multiple model lines (ECMWF blue, HRRR purple, METAR amber) converging. Subtle red/amber highlight on one line of code as if circled by a reviewer. Same charcoal + cyan + signal-green palette. Magnifying-glass or red-pen icon allowed but small and stylized. Conveys "I read the source code so you don't have to."

## Negative prompt (paste alongside if Nano Banana supports it)

> No people, no faces, no hands. No specific company logos (no Anthropic, OpenAI, Polymarket, Hermes, Hetzner, or Railway branding). No crypto-bro aesthetics: no Lambos, no rocket emojis, no neon "to the moon" arrows, no Bitcoin/Ethereum coins, no laser eyes, no lambo-orange or hot-pink gradients. No 3D rendered cubes or generic abstract tech swirls. No stock-photo handshake imagery. No clock or hourglass. No fake/placeholder text that reads as gibberish — keep text fragments either readable or clearly out of focus.

## Practical notes for the user

1. Run the **primary prompt** first. If the cyan equity chart and weather radar overlap awkwardly (common), drop down to **Variation A**.
2. If you want something that screams "skeptical takedown" rather than "neutral analysis," **Variation C** has the strongest editorial energy.
3. After generation, check the central horizontal band — X overlays the article title there in some renders. If the model puts critical visual elements in that band, regenerate.
4. Export as PNG for sharpness on the chart text, JPEG if file size matters.
5. X long-form Article header spec is flexible but typically 1500–1600 px wide × 600–640 px tall works without the platform recropping.
