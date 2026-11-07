# WhatsApp template `connect_season_card`

The first message to an owner goes out **outside** WhatsApp's 24-hour session, so it must be a pre-approved template. Create it in Meta (WhatsApp Manager → Message templates) once per language.

- **Name:** `connect_season_card`
- **Category:** Utility
- **Languages:** Finnish `fi`, Swedish `sv`, Norwegian Bokmål `nb`, English `en`
- **Body:** one variable, `{{1}}`, in the middle of the text (a body can't start or end with a variable).
- **Buttons:** three quick replies.
- **Sample for `{{1}}`:** Meta asks for one when you submit the template; use the sample row below.

| Language | Body | Quick replies |
|---|---|---|
| fi | `Hei! Fonecta Connectin automaattinen avustaja ehdottaa aukioloaikoja: {{1}}. Vastaa alla olevilla painikkeilla.` | Hyväksy · Hyväksy + joka vuosi · Muokkaa |
| sv | `Hej! Fonecta Connects automatiska assistent föreslår öppettider: {{1}}. Svara med knapparna nedan.` | Godkänn · Godkänn + varje år · Ändra |
| nb | `Hei! Fonecta Connects automatiske assistent foreslår åpningstider: {{1}}. Svar med knappene nedenfor.` | Godkjenn · Godkjenn + hvert år · Endre |
| en | `Hi! Fonecta Connect's automated assistant suggests opening hours: {{1}}. Reply with the buttons below.` | Approve · Approve + every year · Edit |

**Sample for `{{1}}`** (fi): `to 24.12. 7.30–13 · pe 25.12. kiinni · pe 1.1. kiinni · ke 6.1. 10–16 · vastaa viimeistään to 17.12`

**Why "automated assistant" is in the body.** The template is the owner's first contact, so it carries the AI Act Art. 50 disclosure. If a shorter text is preferred, the plain version is `Hei! Fonecta Connect ehdottaa aukioloaikoja: {{1}}. Vastaa alla olevilla painikkeilla.` (sv `Hej! Fonecta Connect föreslår öppettider: {{1}}. Svara med knapparna nedan.`; nb `Hei! Fonecta Connect foreslår åpningstider: {{1}}. Svar med knappene nedenfor.`; en `Hi! Fonecta Connect suggests opening hours: {{1}}. Reply with the buttons below.`). With the plain version, the disclosure must then appear in the first in-session message.

## How the code uses it
1. `cs.compose.compose_card_message(...)` puts the one-line summary in `parts["template_params"]` (built by `compose_template_params`, at most 900 characters).
2. **Every parameter** goes through `cs.compose.sanitize_template_param`:
   - line breaks become " · ";
   - tabs and every run of spaces become one space;
   - text over the limit is cut with "…";
   - an empty parameter becomes "-".
3. The trailing full stop is dropped, because the template adds one after `{{1}}`.
4. **Outside the session,** `cs.channels.whatsapp.WhatsAppChannel` sends the template, with payloads `APPROVE|<card_id>`, `APPROVE_EVERY_YEAR|<card_id>` and `EDIT|<card_id>`.
5. **The owner's first tap opens the 24-hour session.** The webhook handler:
   1. calls `outbox.record_inbound(...)`;
   2. sends the full multi-line card again with `router.send(...)`.

   That send goes out as an interactive message, with up to 3 reply buttons. Pass `whatsapp_session=True` to force it.
6. `whatsapp.render_template(lang, params)` shows exactly what the owner will read.
