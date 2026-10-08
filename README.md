# Booking Bot

A WhatsApp assistant that books, moves and cancels appointments for
clinics, plus a web dashboard where each clinic sees and manages its own.
One server runs any number of clinics. Each has its own WhatsApp number,
dashboard and users, and sees only its own data.

- **The bot** answers customers on WhatsApp in Hebrew, English or Russian,
  offers only times that are really free, and books through the same rules
  the dashboard uses.
- **The dashboard** (`/dashboard`) has a calendar, appointments, customers,
  the team and their hours, treatments, statistics and settings. It is in
  Hebrew by default, with English one click away, and works on a phone.
- **Reminders** go out on WhatsApp a day and an hour before each
  appointment.
- **The audit trail** records every conversation step by step in
  `logs/audit.jsonl`. The server checks it every 15 minutes and logs
  anything that went wrong.

Built with FastAPI, PostgreSQL (Supabase in production), GPT-4o and the
Meta WhatsApp Cloud API.

## Setting up a Mac (once)

1. Install Homebrew. Open **Terminal**, paste the command from
   [brew.sh](https://brew.sh), and follow what it prints.
2. Get the code:
   ```bash
   git clone -b claude/exciting-euler-byn7tz https://github.com/StasN16/booking-bot.git
   cd booking-bot
   ```
3. If you have a `.env` file from another computer, copy it into the
   `booking-bot` folder now. Everything already in it is kept.
4. Run the setup:
   ```bash
   ./scripts/setup_mac.sh
   ```
   It installs Python, PostgreSQL and ngrok, creates a local database with
   a demo clinic, and runs the tests. It asks only for what is missing:

   | It asks for | Where to find it |
   |---|---|
   | OpenAI API key | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
   | WhatsApp token | Meta Business settings → System users → your user → Generate token |
   | WhatsApp phone number ID | Meta developer app → WhatsApp → API Setup |
   | WhatsApp verify token | Any word you choose; the same word goes in Meta's webhook settings |
   | Dashboard password | Your choice, or press Enter for a generated one (it is printed) |
   | ngrok authtoken | [dashboard.ngrok.com/get-started/your-authtoken](https://dashboard.ngrok.com/get-started/your-authtoken) |

   Running it again is safe: finished steps are skipped.

## Every day

```bash
cd booking-bot
./scripts/start.sh
```

This starts the server and the ngrok tunnel WhatsApp reaches it through.
It also brings the database up to date, so after `git pull` there is
nothing else to run. Then open **http://localhost:8000**, leave **Email**
empty and sign in with the dashboard password. `Ctrl+C` in the Terminal
stops everything.

If the script says **"The webhook URL changed"**, WhatsApp messages stop
arriving until you paste the new address into Meta: WhatsApp →
Configuration → Callback URL → Edit. Set `NGROK_DOMAIN` in `.env` to your
free fixed ngrok domain and the address never changes again.

Forgot the dashboard password? It is in `.env`, on the `ADMIN_PASSWORD`
line.

## The dashboard

| Page | What it does |
|---|---|
| Clinics | Only for you, the owner. Add clinics, set their WhatsApp numbers and users, and open any clinic's dashboard. |
| Calendar | Day (a column per therapist), week or month. Click free time to book there; click an appointment to open it. Bookings made on WhatsApp appear within a minute. |
| Appointments | Every appointment in a range of dates, with filters by status and therapist and a search by name, phone or treatment. |
| Customers | Everyone who has written to the bot or been booked. Each card shows the customer's history and lets you turn their reminders off. |
| Team | Each therapist's working days and hours, and the treatments they do: all of them, or only the ones ticked. Free times, in the bot and on the dashboard, come from these. |
| Treatments | What the bot offers, with length and price, and who on the team does each. Retiring a treatment hides it from new bookings and keeps the history. |
| Statistics | Appointments, revenue, cancellations, and the split by treatment and therapist, for any range up to a year. |
| Settings | The clinic's details, its password, the dashboard language, and signing out. |

Booking from the dashboard offers only the times the bot would offer, so
the two can never double-book each other. Moving or cancelling does not
message the customer. Instead, the dashboard offers a WhatsApp message,
already written, for you to send.

## Clinics

You sign in as the owner (email left empty) and see every clinic. Each
clinic signs in with its own email and password and sees only itself: no
Clinics page, no other clinic's customers.

### Adding a clinic

1. **Give it a WhatsApp number in Meta.** In your developer app:
   WhatsApp → API Setup → **Add phone number**, then confirm it with the
   code Meta sends. The number cannot also be in use in the WhatsApp app,
   so a new number is simplest.
2. **Copy its Phone number ID** from the same API Setup page.
3. **In the dashboard:** Clinics → **Add clinic**. Fill in the name, phone
   and hours, and paste the Phone number ID. Leave **WhatsApp token**
   empty: it is needed only when the number sits in the clinic's own Meta
   account rather than yours.
4. **Add its user.** The **Users** window opens by itself. A user is the
   email and password the clinic signs in with. Type the clinic's email and
   press **Create user**. Send the clinic its email and the password shown:
   the start password (below), or a random one if you have not set one.
   The first time the clinic signs in, it must choose its own password
   before anything else opens.
5. **Add its team and treatments.** Press **Open dashboard** on the
   clinic's card, or leave this to the clinic. The bot answers on the new
   number at once, and can book as soon as both exist.

The new number reports to the webhook you already set in Meta, so the
webhook settings do not change. If its messages never arrive, check that
WhatsApp → Configuration → Webhook fields has `messages` subscribed.

### Looking after clinics

| To | Do this |
|---|---|
| See a clinic's dashboard | Clinics → **Open dashboard**. On a computer, the menu under the clinic's name switches too. |
| Give a clinic a new password | Clinics → **Users** → **New password**. The old one stops working, and the clinic chooses its own again at its next sign-in. |
| Lock a user out | Clinics → **Users**, switch **Dashboard access** off. They are signed out at once. |
| Pause a clinic | Clinics → **Edit**, switch **Clinic is active** off. Its users, bot and reminders stop; its data stays. |

A clinic changes its own password in Settings. The **main clinic** is the
one `BUSINESS_ID` in `.env` names: the one you see first, and the only one
that may use the WhatsApp number in `.env`.

### The start password

Every new user, and every **New password**, gets the same start password,
so there is nothing to copy each time. Set it once, putting yours in
place of `YOUR-START-PASSWORD`:

```bash
poetry run python scripts/start_password.py YOUR-START-PASSWORD
```

Add `--everyone` to also give it, now, to every clinic user there already
is. Then restart the bot. Since every clinic knows the start password, a
user signing in with it must choose their own before anything else opens;
the Users window marks who has not done so yet. Your owner password is
separate and does not change.

## Settings (`.env`)

`.env` holds this machine's settings and secrets. It is never committed.
`.env.example` lists every key.

| Key | Meaning |
|---|---|
| `DATABASE_URL` | The database. Setup points it at the local one and keeps earlier URLs as comments. |
| `BUSINESS_ID` | The main clinic: the one you see first, and the only one that may use the WhatsApp number in `.env`. Leave it as the seed data set it. |
| `TIMEZONE` | The clinic's timezone. Every time is shown and booked in it. Defaults to `Asia/Jerusalem`. |
| `OPENAI_API_KEY` | For GPT-4o. |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` | The WhatsApp Cloud API connection. The phone ID is the main clinic's number. Every clinic's number uses this token unless the clinic has its own. |
| `ADMIN_PASSWORD`, `JWT_SECRET` | Your owner password (email left empty), and the key every sign-in is signed with. If either is blank, the dashboard stays locked. |
| `JWT_HOURS` | How long a dashboard sign-in lasts. Defaults to 12. |
| `CLINIC_START_PASSWORD` | The password new clinic users start with (see [The start password](#the-start-password)). Blank: a random one each time. |
| `REPLY_DELAY_SECONDS` | A short pause before the bot answers, so it reads as typed. Defaults to 0.5. |
| `NGROK_DOMAIN` | Optional fixed ngrok domain, so the webhook URL never changes. |
| `REMINDERS_ENABLED`, `REMINDER_POLL_MINUTES` | The reminder loop. Defaults to on, every 10 minutes. |
| `AUDIT_WATCH_MINUTES` | How often the server checks its own audit trail. 0 turns the checks off. |

Check that the credentials work, without changing anything:

```bash
poetry run python scripts/configure.py --check
```

## Scripts

| Script | What it does |
|---|---|
| `scripts/setup_mac.sh` | One-time Mac setup. |
| `scripts/start.sh` | Starts the server and the tunnel. |
| `scripts/configure.py` | Fills in missing `.env` values and tests the credentials (`--check` only tests). |
| `scripts/start_password.py` | Sets the password new clinic users start with; `--everyone` gives it to existing users too. |
| `scripts/setup_local_db.py` | Creates the local database, applies migrations and adds the demo clinic. |
| `scripts/migrate_supabase.py` | Applies migrations to Supabase without touching `.env`. |
| `scripts/integration_test.py` | Runs the full booking flow and the dashboard API against a real database, then cleans up. |
| `scripts/analyze_audit.py` | Reports problems in the audit trail. `--show TRACE_ID` shows one conversation step by step. |

## Tests

```bash
poetry run pytest                              # unit tests, no database needed
poetry run python scripts/integration_test.py  # against the database in .env
```

## How it is organised

```
app/
  main.py           the server: routes, dashboard files, reminder and audit loops
  api/v1/           webhook (WhatsApp), auth, appointments, customers, therapists,
                    treatments, business: the API the dashboard calls;
                    platform: the owner's clinics and their users
  services/         conversation (the bot), ai_engine (GPT-4o), booking,
                    availability, reminders, whatsapp, accounts (clinic users),
                    audit analysis
  core/             models, schemas, database, timezone helpers, audit trail,
                    tenancy (which clinic is being served, and its number)
  static/dashboard/ the dashboard: plain JavaScript modules, no build step
migrations/         Alembic database migrations
scripts/            setup, start, checks
tests/              unit tests
```

The bot and the dashboard share one booking service
(`app/services/booking.py`) and one availability calculation
(`app/services/availability.py`), so both follow the same rules.

Every request, WhatsApp message and reminder is handled on behalf of one
clinic, and everything that reads or writes clinic data asks
`current_business_id()` (`app/core/tenancy.py`) which one that is. A
message finds its clinic by the WhatsApp number it was sent to; a
dashboard request, by who is signed in.

The dashboard builds everything on the page from plain text, never from
HTML, and the server forbids inline scripts. A customer's name or note can
only ever be displayed, never run.

## When something is wrong

| Problem | What to do |
|---|---|
| The bot does not answer | Is `start.sh` running? Did it warn that the webhook URL changed? Then check the token: `poetry run python scripts/configure.py --check`. |
| One clinic's bot does not answer | On the Clinics page, check that the clinic is on and its Phone number ID matches Meta's. The Terminal running `start.sh` names any number no clinic has. |
| A clinic cannot sign in | Clinics → **Users**: is **Dashboard access** on? If they lost the password, press **New password**. |
| `configure.py --check` says Meta rejected the WhatsApp token | Generate a new token for the system user in Meta Business settings and put it in `.env` as `WHATSAPP_TOKEN`. |
| `zsh: command not found: poetry` | That Terminal window is older than the setup. Open a new one (`Cmd+N`), or first run `eval "$(/opt/homebrew/bin/brew shellenv)"`. |
| "Port 8000 is already in use" | The bot is already running in another Terminal window. Use that one, or stop it there with `Ctrl+C`. |
| The dashboard says sign-in is not set up | Run `./scripts/setup_mac.sh`, then start the server again. |
| Supabase cannot be reached | Some home networks block port 5432. Use the local database, or a phone hotspot for Supabase work. |
| Something went wrong in a conversation | `poetry run python scripts/analyze_audit.py` lists what failed and where. |

## Known limits and next steps

- **Reminders and WhatsApp's 24-hour rule.** WhatsApp lets a business
  send free text only within 24 hours of the customer's last message.
  Outside that window a reminder needs a message template approved by
  Meta. Without one, reminders to customers who booked days ahead are
  refused. This is planned for before going live (step 12).
- **Turning a customer's reminders off** stops reminders only. The bot
  still answers that customer.
- **Clinics are added by you**, on the Clinics page. There is no sign-up
  page or billing for clinics yet.
- **Step 11:** syncing each clinic's appointments to its Google Calendar.
- **Step 12:** running on Railway with Supabase, instead of on this Mac.
