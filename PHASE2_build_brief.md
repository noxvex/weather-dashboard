# Phase 2 — Accounts, Login, Roles & Lockout

**Paste this whole file into a fresh Claude Code session in PyCharm, then say "build now" there.**
Build only Phase 2. Do not build Aktuality or forecast pages yet.
UI text visible to users = Czech. Code, comments, variable names = English.

---

## 1. Roles (three tiers)

Extend Django's built-in User with a `role` field. Do NOT build custom auth from scratch — use Django's auth system.

| Role     | Who      | Can do                                                        |
|----------|----------|--------------------------------------------------------------|
| `admin`  | noxvex   | Django superuser. Creates all accounts. Full control.        |
| `leader` | yxes     | Can edit/delete ANY note (moderation). Notes shown in red (used later, in Aktuality phase). |
| `worker` | ~8 others| Can add/edit/delete only their OWN notes (enforced later).   |

- Store role as a `CharField` with choices `("admin","leader","worker")`, default `"worker"`.
- Simplest clean approach: a `Profile` model with `OneToOneField(User)` + `role`, OR a custom user model. **Recommend a custom user model (`AbstractUser` + role field)** since the project is early and this avoids painful migration later. Explain the tradeoff to me before writing it.

## 2. Accounts

- Usernames = lowercase first name (e.g. `noxvex`, `yxes`, `petr`).
- **Admin creates every account manually** via Django admin. No self-registration.
- Initial password set by admin. **No self-service password reset** — if a user forgets, admin resets manually.
- Use Django's default password hashing (PBKDF2). *(Original spec said bcrypt — tell me if you specifically want bcrypt configured; PBKDF2 is Django's secure default and needs zero extra setup.)*

## 3. Login page

- A single login page (username + password), text in Czech.
- **Everything else in the app requires login** — logged-out users can ONLY reach the login page. Use `@login_required` / `LoginRequiredMixin` globally.
- On wrong password: plain Czech error. Include small helper text: "Zapomenuté heslo? Kontaktujte správce (noxvex)."

## 4. Lockout + email alert  ⟵ CONTAINS MY ASSUMPTIONS — confirm

- After **5 failed login attempts** on the same username → lock that account.
- **Lockout is permanent until admin unlocks** it in Django admin (a boolean field like `is_locked`). *(Assumption — say if you'd rather it auto-expire after e.g. 30 min.)*
- **If the username was valid** but attempts kept failing to 5 → **send an email to noxvex**.
  - Email includes: the targeted username, timestamp, and a note that the account is now locked. *(Assumption.)*
  - If the username does NOT exist, do not email (avoids spam from random bots).
- Locked user sees a Czech message telling them to contact the admin.
- **Email setup:** use Django's email backend via environment variables (`EMAIL_HOST`, `EMAIL_HOST_USER`, etc. in `.env`). For local testing, use the console email backend so nothing real is sent until we configure SMTP. Tell me what to put in `.env`.

## 5. Tutoring requirement (I'm a Python beginner)

As you build, briefly explain:
- What a custom user model is and why we chose it.
- What a migration is when you run `makemigrations` / `migrate`.
- How `@login_required` protects a page.
Keep explanations short — a sentence or two each, not a lecture.

## 6. Definition of "Phase 2 done"

1. `noxvex` superuser exists; can log into Django admin.
2. Admin can create `yxes` (leader) and at least one `worker` account with a role.
3. Logged-out users are bounced to the login page for every URL except login.
4. 5 wrong attempts locks the account; a valid-username lockout triggers an email (visible in console backend during testing).
5. Admin can unlock a locked account from Django admin.
6. Migrations committed to Git with message "Phase 2: accounts, login, roles, lockout".

## 7. Do NOT do in this phase
- No Aktuality / notes feature.
- No forecast detail pages or graphs.
- No automatic app-generated notes.
- No real SMTP email (console backend only until I provide credentials).